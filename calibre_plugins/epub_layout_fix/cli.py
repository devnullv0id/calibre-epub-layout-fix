#!/usr/bin/env python3
"""The command line interface.

calibre hands a plugin the command line through :meth:`Plugin.cli_main`, reached as::

    calibre-debug -r "EPUB Layout Fix" -- --help

Everything here runs headless: no QApplication, no toolbar, no job queue. The work itself is
:func:`jobs.process_book`, the very function the toolbar buttons run, so a book repaired from a
script and a book repaired from the button come out identical.

Books are always processed on a copy and only moved over the original once every stage has
succeeded, so an interrupted run cannot leave a half-polished file behind.

A result is kept when the layout fix changed something, when a conversion produced a book that did
not exist before, or when a rewriting stage was asked for by name (``--polish``, ``--beautify``).
Not merely because a stage touched the bytes: polish rewrites a book every time it runs, and a
command line that wrote every book on every pass would churn a library forever - and, worse,
replace each book's ``ORIGINAL_EPUB`` with the previous run's output until the real original was
gone.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback

from calibre_plugins.epub_layout_fix import fixer, jobs

__license__ = 'GPL v3'

PROG = 'calibre-debug -r "EPUB Layout Fix" --'

EPILOG = """\
examples:
  # what would change, without touching anything
  %(prog)s --report book.epub

  # repair a directory of books in place, keeping the originals as .bak
  %(prog)s --recursive --backup ~/Books

  # convert and repair, leaving the sources alone
  %(prog)s --convert --output-dir out/ *.azw3

  # repair everything in a library that a report flags, writing back to the library
  %(prog)s --library ~/Calibre --search "formats:EPUB" --report
  %(prog)s --library ~/Calibre --search "formats:EPUB"

exit status:
  0  nothing failed
  1  at least one book failed
  2  the command line was wrong
  3  --fail-on-change was given and a book needs work
"""

#: (settings key, parser destination). The engine settings the command line can override.
SETTING_FLAGS = (
    ('fix_images', 'fix_images'),
    ('fix_covers', 'fix_covers'),
    ('dark_cover', 'dark_cover'),
    ('preserve_anchors', 'preserve_anchors'),
    ('fix_captioned', 'fix_captioned'),
    ('fix_multi_image', 'fix_multi_image'),
    ('min_width_percent', 'min_width_percent'),
    ('cover_color', 'cover_color'),
)


# -- argument parsing -------------------------------------------------------------------

def _pair(group, name, dest, help_text):
    """A ``--thing`` / ``--no-thing`` pair that defaults to "leave the saved setting alone".

    The default has to be ``None`` rather than ``True``/``False``: the point of these is to
    override the stored settings only where the user actually said something.
    """
    import argparse
    group.add_argument('--' + name, dest=dest, action='store_true', default=None, help=help_text)
    group.add_argument('--no-' + name, dest=dest, action='store_false', default=None,
                       help=argparse.SUPPRESS)


def build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog=PROG, add_help=True,
        description='Repair full-page images and covers in EPUB books.',
        epilog=EPILOG % {'prog': PROG},
        formatter_class=argparse.RawDescriptionHelpFormatter)

    p.add_argument('paths', nargs='*', metavar='PATH',
                   help='EPUB files, or directories to scan for them; with --convert, '
                        'directories give up their convertible formats too')
    p.add_argument('--recursive', action='store_true',
                   help='descend into sub-directories of a PATH (default: top level only)')

    mode = p.add_argument_group('what to do')
    ex = mode.add_mutually_exclusive_group()
    ex.add_argument('--report', action='store_true',
                    help='list what would change, page by page, and write nothing')
    ex.add_argument('-n', '--dry-run', action='store_true',
                    help='do the whole job, verify the result, then throw it away')
    mode.add_argument('--fail-on-change', action='store_true',
                      help='exit 3 if any book needs work (for scripts and CI)')

    lib = p.add_argument_group('library mode',
                               'work on books in a calibre library. Close calibre first: it '
                               'keeps the library in memory and will not see these changes.')
    lib.add_argument('--library', metavar='PATH', help='path to the calibre library folder')
    lib.add_argument('--search', metavar='QUERY',
                     help='a calibre search expression selecting the books')
    lib.add_argument('--ids', metavar='LIST', help='comma-separated book ids')
    lib.add_argument('--all', action='store_true', dest='all_books',
                     help='every book in the library')

    out = p.add_argument_group('output')
    out.add_argument('--output-dir', metavar='DIR',
                     help='write results here instead of over the input (file mode only)')
    out.add_argument('--backup', action='store_true',
                     help='keep the original as PATH.bak when writing in place')
    out.add_argument('--json', action='store_true', dest='as_json',
                     help='print one JSON object per run on stdout instead of a summary')
    out.add_argument('-v', '--verbose', action='count', default=0,
                     help='show the per-page ledger; twice also shows calibre\'s own output')
    out.add_argument('-q', '--quiet', action='store_true', help='only report failures')

    pipe = p.add_argument_group('pipeline')
    pipe.add_argument('--convert', action='store_true',
                      help='convert non-EPUB input to EPUB first')
    pipe.add_argument('--from-format', metavar='FMT',
                      help='library mode only: convert from this format rather than the best '
                           'one (a named file already says which format it is)')
    pipe.add_argument('--epub-version', choices=('2', '3'), dest='epub_version',
                      help="target EPUB version ('2' means leave the version alone)")
    _pair(pipe, 'polish', 'polish', "run calibre's polish stage with its saved operations")
    _pair(pipe, 'beautify', 'beautify', 'pretty-print every file in the book')

    st = p.add_argument_group('layout settings',
                              'each defaults to the setting saved in calibre')
    _pair(st, 'images', 'fix_images', 'rebuild full-page image pages')
    _pair(st, 'covers', 'fix_covers', 'repair the cover page')
    _pair(st, 'dark-cover', 'dark_cover', 'give the cover dark letterbox bands')
    _pair(st, 'preserve-anchors', 'preserve_anchors', 'keep link targets on rebuilt pages')
    _pair(st, 'captioned', 'fix_captioned', 'also rebuild pages whose image has a caption')
    _pair(st, 'multi-image', 'fix_multi_image', 'also rebuild pages holding several images')
    st.add_argument('--min-width', type=float, metavar='PERCENT', dest='min_width_percent',
                    help='an image must be at least this wide to count as full-page')
    st.add_argument('--cover-color', metavar='#RRGGBB', dest='cover_color',
                    help='the letterbox colour')
    st.add_argument('--defaults', action='store_true',
                    help="ignore the settings saved in calibre and start from the plugin's own")

    return p


# -- settings ---------------------------------------------------------------------------

def _saved(name, fallback, warnings):
    """Read one thing out of the plugin's saved configuration, tolerating a headless failure.

    ``config`` reaches Qt and ``polish_settings`` reaches calibre's conversion preferences. Both
    are fine in practice, but a command line that dies because it could not read a preference
    would be a poor trade, so every lookup can fall back.
    """
    try:
        from calibre_plugins.epub_layout_fix import config
        return getattr(config, name)()
    except Exception as e:                                     # noqa: BLE001
        warnings.append('could not read the saved %s (%s); using the built-in default'
                        % (name, e))
        return fallback


def _saved_polish(warnings):
    """The saved polish operations, minus the ones that reach off the machine."""
    try:
        from calibre_plugins.epub_layout_fix import config
        return config.polish_settings(automatic=True)
    except Exception as e:                                     # noqa: BLE001
        warnings.append('could not read the saved polish settings (%s); skipping polish' % e)
        return False, {}


def resolve_settings(opts, warnings):
    """The engine settings: the saved ones, then whatever the command line overrode."""
    if opts.defaults:
        settings = dict(fixer.DEFAULT_SETTINGS)
    else:
        settings = _saved('current_settings', dict(fixer.DEFAULT_SETTINGS), warnings)
    for key, dest in SETTING_FLAGS:
        value = getattr(opts, dest, None)
        if value is not None:
            settings[key] = value
    return settings


def resolve_pipeline(opts, warnings):
    """-> ``(polish_ops or None, target_version, beautify)``.

    The polish operations come back as they do for an automatic run, not as the conversion window
    left them. "Embed referenced fonts" copies matching fonts off this computer into the book and
    "Download external resources" fetches remote URLs; ``config.AUTO_POLISH_EXCLUDED`` already
    rules both out of anything that fires unattended, and a command line in a script is exactly
    that. calibre's own Polish tool is there for anyone who wants them.
    """
    if opts.defaults:
        polish_on, polish_ops, beautify, version = False, {}, False, '3'
    else:
        polish_on, polish_ops = _saved_polish(warnings)
        beautify = _saved('beautify_enabled', False, warnings)
        version = _saved('target_epub_version', '3', warnings)

    if opts.polish is not None:
        polish_on = opts.polish
        if polish_on and not polish_ops:
            polish_ops = {'upgrade_book': True}
    if opts.beautify is not None:
        beautify = opts.beautify
    if opts.epub_version:
        version = opts.epub_version
    return (dict(polish_ops) if polish_on else None), version, bool(beautify)


# -- gathering work ---------------------------------------------------------------------

def collect_files(paths, recursive, warnings, extra_formats=()):
    """-> the files to work on, in the order they were named. Directories expand to books.

    A directory normally gives up its EPUBs only. ``extra_formats`` - the convertible formats,
    passed when ``--convert`` is on - widens that, because ``--convert --recursive ~/Books`` that
    walked straight past every AZW3 would not be doing what it says.

    A file named on the command line is always taken as given: someone who types a filename has
    already decided.
    """
    wanted = {'.epub'} | {'.' + f.lower() for f in extra_formats}
    found, seen = [], set()

    def add(p):
        # dedupe on the resolved path, but keep the path as it was given: echoing an absolute
        # path back at someone who typed a relative one makes the output much harder to read
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            found.append(os.path.normpath(p))

    def scan(root, names):
        for f in sorted(names):
            full = os.path.join(root, f)
            if os.path.isfile(full) and os.path.splitext(f)[1].lower() in wanted:
                add(full)

    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    scan(root, files)
            else:
                scan(p, os.listdir(p))
        elif os.path.isfile(p):
            add(p)
        else:
            warnings.append('no such file or directory: %s' % p)
    return _drop_shadowed(found)


def _drop_shadowed(files):
    """Drop a convertible file whose EPUB is also in the list.

    A directory holding both ``book.azw3`` and ``book.epub`` would otherwise convert the AZW3
    straight over the EPUB that is sitting right there - and, because the list is sorted, do it
    before the EPUB itself is even looked at.
    """
    epubs = {os.path.normcase(os.path.splitext(f)[0]) for f in files
             if f.lower().endswith('.epub')}
    return [f for f in files
            if f.lower().endswith('.epub')
            or os.path.normcase(os.path.splitext(f)[0]) not in epubs]


def resolve_ids(db, opts):
    """-> the book ids selected by --ids / --search / --all."""
    if opts.ids:
        ids = []
        for chunk in opts.ids.replace(',', ' ').split():
            try:
                ids.append(int(chunk))
            except ValueError:
                raise ValueError('not a book id: %r' % chunk)
        return ids
    if opts.search:
        return sorted(db.search(opts.search))
    return sorted(db.all_book_ids())


# -- running ----------------------------------------------------------------------------

def _digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _worth_keeping(result, converting, work, before, ctx):
    """Is there an output here that should replace what was there before?

    Byte-difference alone is not the test. calibre's polish rewrites a book every time it runs,
    so a command line keying off the bytes would rewrite every book on every pass - and in a
    library that means ``save_original_format`` replacing the pristine ``ORIGINAL_EPUB`` with the
    last run's output, over and over, until the real original is gone. Measured: three passes left
    ``ORIGINAL_EPUB`` holding the output of pass two.

    So the reasons to keep something are the deliberate ones: the layout fix did something, or the
    book did not exist until this run converted it, or the user named a stage whose whole purpose
    is to rewrite. Even then the bytes still have to differ, so a polish that changed nothing does
    not cost the book its original.
    """
    if not (converting or result.get('changed') or ctx['keep_rewrites']):
        return False
    return bool(converting or (os.path.exists(work) and _digest(work) != before))


def _log_for(opts):
    from calibre.utils.logging import ERROR, INFO, Log
    return Log(level=INFO if opts.verbose > 1 else ERROR)


class _Notifier(object):
    """Stands in for calibre's job notification queue, printing stages instead of drawing them."""

    def __init__(self, out, enabled, title):
        self.out, self.enabled, self.title = out, enabled, title

    def put(self, item):
        if self.enabled:
            frac, msg = item
            self.out.write('   %3d%%  %s\n' % (round(frac * 100), msg))


def run_one(job, opts, out):
    """Report on or process a single staged job dict. -> the usual result dict."""
    log = _log_for(opts)
    if opts.report:
        return jobs.run_report(job, log=log)
    notifier = _Notifier(out, opts.verbose > 0 and not opts.as_json, job.get('title'))
    return jobs.run_single(job, notifications=notifier, log=log)


NOT_EPUB = 'not an EPUB (use --convert)'
NO_REPORT = ('not an EPUB; --report only examines EPUBs. Use --dry-run to see what a real run '
             'would produce - it converts into a temporary copy and keeps nothing.')


def process_files(paths, ctx, opts, out):
    """Work through standalone files. -> list of result dicts.

    Each book is printed as it finishes rather than at the end, and one book's failure is recorded
    and stepped over: a locked file three hundred books into a run should cost that book, not the
    run and every result already collected.
    """
    results, claimed = [], {}
    for n, path in enumerate(paths, 1):
        try:
            result = _one_file(path, ctx, opts, out, claimed)
        except Exception:                                      # noqa: BLE001 - one book, not the run
            result = _failed(path, traceback.format_exc())
        results.append(result)
        _emit(result, opts, out, n, len(paths))
    return results


def _one_file(path, ctx, opts, out, claimed):
    converting = os.path.splitext(path)[1].lower() != '.epub'
    if converting:
        if not opts.convert:
            return _skipped(path, NOT_EPUB)
        if opts.report:
            # run_report only ever opens a zip, so pointing it at a MOBI reports a BadZipFile
            # that says nothing about the book
            return _skipped(path, NO_REPORT)

    base = os.path.splitext(os.path.basename(path))[0] + '.epub'
    if opts.output_dir:
        final = os.path.join(opts.output_dir, base)
    elif converting:
        final = os.path.normpath(os.path.join(os.path.dirname(path), base))
    else:
        final = path

    if not (opts.report or opts.dry_run):
        # Two inputs can share a basename, and a conversion can land on a book already in the
        # list. Losing the first result silently is the worst of the available outcomes.
        key = os.path.normcase(os.path.abspath(final))
        if key in claimed:
            return _failed(path, 'would overwrite the output of %s' % claimed[key])
        claimed[key] = path

    tmpdir = tempfile.mkdtemp(prefix='eplf-cli-')
    work = os.path.join(tmpdir, base)
    try:
        before = None
        if converting:
            job_convert_from = path
        else:
            shutil.copy2(path, work)
            before = _digest(work)
            job_convert_from = None

        job = dict(ctx['job'], path=work, title=os.path.basename(path),
                   convert_from=job_convert_from)
        if opts.report:
            # nothing is written, so the report reads the original rather than a copy
            job['path'] = path
        result = run_one(job, opts, out)
        result['source'] = path

        if opts.report or opts.dry_run or result.get('error'):
            return result

        changed = _worth_keeping(result, converting, work, before, ctx)
        result['changed_on_disk'] = changed
        if changed:
            # An existing .bak is from an earlier run and is therefore closer to the original
            # than anything this one could save. Leave it alone.
            if opts.backup and final == path and os.path.exists(path):
                if os.path.exists(path + '.bak'):
                    result['backup'] = path + '.bak (kept from an earlier run)'
                else:
                    shutil.copy2(path, path + '.bak')
                    result['backup'] = path + '.bak'
            d = os.path.dirname(final)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            shutil.move(work, final)
            result['written'] = final
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def process_library(opts, ctx, out):
    """Work through books in a calibre library, writing the results back. -> result dicts."""
    from calibre.library import db as open_library

    # calibre happily *creates* a library at a path that has none, so a mistyped --library would
    # otherwise leave an empty library behind and report that it fixed nothing.
    if not os.path.exists(os.path.join(opts.library, 'metadata.db')):
        raise ValueError('not a calibre library (no metadata.db): %s' % opts.library)

    library = open_library(opts.library)
    db = library.new_api
    try:
        ids = resolve_ids(db, opts)
        results = []
        for n, book_id in enumerate(ids, 1):
            try:
                result = _one_book(db, book_id, ctx, opts, out)
            except Exception:                                  # noqa: BLE001 - one book, not the run
                result = _failed('#%s' % book_id, traceback.format_exc(), book_id=book_id)
            results.append(result)
            _emit(result, opts, out, n, len(ids))
        return results
    finally:
        try:
            library.close()
        except Exception:                                      # noqa: BLE001
            pass


def _one_book(db, book_id, ctx, opts, out):
    fmts = {f.upper() for f in (db.formats(book_id) or ())}
    title = db.field_for('title', book_id) or str(book_id)
    label = '%s (#%d)' % (title, book_id)

    src_fmt = 'EPUB' if 'EPUB' in fmts else None
    if src_fmt is None:
        if not opts.convert:
            return _skipped(label, 'no EPUB format (use --convert)', book_id=book_id)
        src_fmt = jobs.pick_source(fmts, opts.from_format)
        if src_fmt is None:
            return _skipped(label, 'no convertible format', book_id=book_id)
        if opts.report:
            # the EPUB does not exist yet, and run_report only reads one
            return _skipped(label, NO_REPORT, book_id=book_id)
    elif opts.convert and opts.from_format:
        src_fmt = jobs.pick_source(fmts, opts.from_format)
        if src_fmt != 'EPUB' and opts.report:
            return _skipped(label, NO_REPORT, book_id=book_id)

    converting = src_fmt != 'EPUB'
    tmpdir = tempfile.mkdtemp(prefix='eplf-cli-')
    try:
        work = os.path.join(tmpdir, 'book.epub')
        job = dict(ctx['job'], path=work, title=title, book_id=book_id)
        if converting:
            src = os.path.join(tmpdir, 'source.' + src_fmt.lower())
            db.copy_format_to(book_id, src_fmt, src)
            job['convert_from'] = src
            job['recommendations'] = _library_metadata(db, book_id, out)
            before = None
        else:
            db.copy_format_to(book_id, 'EPUB', work)
            before = _digest(work)

        result = run_one(job, opts, out)
        result['source'] = label

        if opts.report or opts.dry_run or result.get('error'):
            return result

        changed = _worth_keeping(result, converting, work, before, ctx)
        result['changed_on_disk'] = changed
        if changed:
            # The same call calibre's Polish action makes, so Restore original works - but only
            # when there is nothing saved yet. calibre overwrites, and an ORIGINAL_EPUB from an
            # earlier run is the one worth keeping.
            if 'ORIGINAL_EPUB' not in {f.upper() for f in (db.formats(book_id) or ())}:
                try:
                    db.save_original_format(book_id, 'EPUB')
                except Exception:                              # noqa: BLE001 - no EPUB yet
                    pass
            with open(work, 'rb') as f:
                db.add_format(book_id, 'EPUB', f, run_hooks=False)
            result['written'] = 'library #%d' % book_id
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _library_metadata(db, book_id, out):
    """The library's metadata as a conversion recommendation, exactly as the toolbar does it."""
    try:
        from calibre.customize.conversion import OptionRecommendation
        from calibre.ebooks.metadata.opf2 import metadata_to_opf
        from calibre.ptempfile import PersistentTemporaryFile

        mi = db.get_metadata(book_id)
        # supplying a cover makes calibre replace the publisher's cover page with a regenerated
        # title page and then leave the navigation pointing at the page it deleted
        mi.cover = None
        mi.cover_data = (None, None)
        opf = PersistentTemporaryFile('.opf')
        data = metadata_to_opf(mi)
        opf.write(data if isinstance(data, bytes) else data.encode('utf-8'))
        opf.close()
        return [('read_metadata_from_opf', opf.name, OptionRecommendation.HIGH)]
    except Exception as e:                                     # noqa: BLE001 - metadata is a bonus
        out.write('   warning: could not pass the library metadata to the conversion: %s\n' % e)
        return []


def _result(source, book_id=None, **extra):
    """A result dict for a book that never reached the pipeline, shaped like one that did."""
    d = jobs.as_dict(str(source), [], None, None)
    d.update({'source': str(source), 'title': os.path.basename(str(source)),
              'book_id': book_id})
    d.update(extra)
    return d


def _skipped(source, reason, book_id=None):
    return _result(source, book_id, skipped_reason=reason)


def _failed(source, message, book_id=None):
    return _result(source, book_id, error=message)


# -- output -----------------------------------------------------------------------------

def _describe(result):
    bits = []
    if result.get('image_pages'):
        bits.append('%d image page(s)' % result['image_pages'])
    if result.get('svg_repaired'):
        bits.append('%d svg repair(s)' % result['svg_repaired'])
    if result.get('cover_fixed'):
        bits.append('cover')
    if result.get('dead_links'):
        bits.append('%d dead link(s)' % result['dead_links'])
    return ', '.join(bits) or 'no changes needed'


STAGE_NAMES = {'convert': 'the conversion', 'polish': 'polishing',
               'upgrade': 'the EPUB 3 upgrade', 'beautify': 'beautifying'}


def _stages(result):
    """Which pipeline stages ran, for the line about a book the layout fix left alone."""
    names = [STAGE_NAMES[s[0]] for s in result.get('steps', ()) if s[0] in STAGE_NAMES and s[1]]
    return ', '.join(names) or 'the pipeline'


def _emit(result, opts, out, n=None, total=None):
    """Print one finished book, as it finishes.

    Live rather than collected at the end: a run over a few hundred books that says nothing for
    twenty minutes is indistinguishable from one that has hung.
    """
    if opts.as_json:
        return                                                 # the whole run is dumped at the end
    tag = '' if not total or total < 2 else '[%*d/%d] ' % (len(str(total)), n, total)

    if result.get('skipped_reason'):
        if not opts.quiet:
            out.write('%sSKIP  %s: %s\n' % (tag, result['source'], result['skipped_reason']))
        return
    if result.get('error'):
        out.write('%sFAIL  %s: %s\n'
                  % (tag, result['source'], result['error'].strip().splitlines()[-1]))
        return
    if opts.quiet:
        return

    # only worth naming the destination when it is not the file we were handed
    written = result.get('written')
    where = '' if not written or written == result['source'] else ' -> %s' % written
    if result.get('changed'):
        verb = 'would fix' if (opts.report or opts.dry_run) else 'fixed'
        out.write('%s  ok  %s: %s - %s%s\n'
                  % (tag, result['source'], verb, _describe(result), where))
    elif result.get('changed_on_disk'):
        # the layout was already sound, but converting, polishing, upgrading or beautifying
        # still rewrote the book, and that output is worth keeping
        out.write('%s  ok  %s: no layout changes; rewritten by %s%s\n'
                  % (tag, result['source'], _stages(result), where))
    else:
        out.write('%s  ok  %s: nothing to do\n' % (tag, result['source']))

    if opts.verbose:
        for entry in result.get('ledger', ()):
            out.write('        %-7s %-14s %s%s\n'
                      % (entry.get('action', ''), entry.get('category', ''), entry.get('page', ''),
                         '  (%s)' % entry['reason'] if entry.get('reason') else ''))
    for problem in result.get('problems', ()):
        out.write('        !! %s\n' % problem)


def report(results, opts, warnings, out):
    """Close the run off: the summary, or the whole thing as JSON. -> the exit code.

    The per-book lines have already been printed by :func:`_emit` as each one finished.
    """
    failed = [r for r in results if r.get('error')]
    needs = [r for r in results if r.get('changed') and not r.get('error')]

    if opts.as_json:
        json.dump({'books': results, 'warnings': warnings,
                   'failed': len(failed), 'needing_work': len(needs)},
                  sys.stdout, indent=2, default=str)
        sys.stdout.write('\n')
    elif not opts.quiet:
        noun = 'needing work' if (opts.report or opts.dry_run) else 'with layout repairs'
        out.write('\n%d book(s), %d %s, %d failed\n'
                  % (len(results), len(needs), noun, len(failed)))
        written = [r for r in results if r.get('written')]
        if written and not (opts.report or opts.dry_run):
            out.write('%d file(s) written\n' % len(written))
        if opts.dry_run:
            out.write('dry run: nothing was written\n')

    if not opts.as_json:
        # under --json they are already in the envelope, and repeating them afterwards puts
        # loose text where a parser expects the document to end
        for w in warnings:
            out.write('warning: %s\n' % w)

    if failed:
        return 1
    if opts.fail_on_change and needs:
        return 3
    return 0


# -- entry point ------------------------------------------------------------------------

def main(argv=None, out=None):
    """-> the process exit code. ``argv`` excludes the program name."""
    out = out or sys.stderr
    parser = build_parser()
    opts = parser.parse_args(sys.argv[1:] if argv is None else list(argv))

    if opts.library and opts.paths:
        parser.error('give either PATHs or --library, not both')
    if not opts.library and not opts.paths:
        parser.error('nothing to do: name some files, or a --library')
    if opts.library and not (opts.ids or opts.search or opts.all_books):
        parser.error('--library needs one of --ids, --search or --all')
    if opts.output_dir and opts.library:
        parser.error('--output-dir only applies to files, not to a library')
    if opts.backup and opts.output_dir:
        parser.error('--backup only applies when writing over the input')
    if opts.backup and opts.library:
        parser.error('--backup only applies to files; a library book keeps its ORIGINAL_EPUB, '
                     'which calibre\'s "Restore original" reads')

    # Flags that cannot do anything in the mode they were given with. Accepting them silently is
    # how someone ends up believing a --dry-run wrote something to --output-dir.
    if (opts.report or opts.dry_run) and (opts.backup or opts.output_dir):
        parser.error('--backup and --output-dir write files, which %s never does'
                     % ('--report' if opts.report else '--dry-run'))
    if opts.fail_on_change and not (opts.report or opts.dry_run):
        parser.error('--fail-on-change reports books that still need work, so it belongs with '
                     '--report or --dry-run; after a real run they have been repaired')

    if opts.output_dir and os.path.exists(opts.output_dir) and not os.path.isdir(opts.output_dir):
        parser.error('--output-dir is not a directory: %s' % opts.output_dir)
    if opts.min_width_percent is not None:
        v = opts.min_width_percent
        if not (0 <= v <= 100):                                # also rejects nan, which fails both
            parser.error('--min-width must be a percentage between 0 and 100, not %r' % v)
    if opts.cover_color is not None:
        import re
        if not re.match(r'^#[0-9A-Fa-f]{6}$', opts.cover_color):
            # the settings panel silently corrects this to #000000; a command line should say so
            parser.error('--cover-color must look like #RRGGBB, not %r' % opts.cover_color)

    warnings = []
    settings = resolve_settings(opts, warnings)
    polish_ops, version, beautify = resolve_pipeline(opts, warnings)
    ctx = {
        'job': {
            'settings': settings,
            'polish_ops': polish_ops,
            'target_version': version,
            'beautify': beautify,
            'dry_run': bool(opts.dry_run),
        },
        # asked for by name on this run, so its output is the point rather than preparation
        'keep_rewrites': opts.polish is True or opts.beautify is True,
    }

    try:
        if opts.library:
            results = process_library(opts, ctx, out)
        else:
            files = collect_files(opts.paths, opts.recursive, warnings,
                                  jobs.SOURCE_PREFERENCE if opts.convert else ())
            if not files:
                warnings.append('no books found')
                # still goes through report(), so --json always produces parseable output
                report([], opts, warnings, out)
                return 2
            results = process_files(files, ctx, opts, out)
    except ValueError as e:
        parser.error(str(e))
    except KeyboardInterrupt:
        out.write('\ninterrupted; nothing further was written\n')
        return 1

    return report(results, opts, warnings, out)


def cli_main(args):
    """calibre's hook. ``args[0]`` is the plugin name, the rest is the command line."""
    raise SystemExit(main(args[1:]))
