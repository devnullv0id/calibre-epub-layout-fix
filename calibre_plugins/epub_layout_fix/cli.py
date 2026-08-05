#!/usr/bin/env python3
"""The command line interface.

calibre hands a plugin the command line through :meth:`Plugin.cli_main`, reached as::

    calibre-debug -r "EPUB Layout Fix" -- --help

Everything here runs headless: no QApplication, no toolbar, no job queue. The work itself is
:func:`jobs.process_book`, the very function the toolbar buttons run, so a book repaired from a
script and a book repaired from the button come out identical.

Two things it does deliberately differently from the GUI:

* Books are always processed on a copy and only moved over the original once every stage has
  succeeded, so an interrupted run cannot leave a half-polished file behind.
* The output is kept when *any* stage changed the file, not only when the layout fix did. From a
  command line, ``--convert book.mobi`` producing nothing because the book happened to need no
  layout repair would simply be wrong.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile

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
                   help='EPUB files, or directories to scan for them')
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

    lib = p.add_argument_group('library mode', 'work on books in a calibre library')
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
                      help='in library mode, convert from this format rather than the best one')
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
    """-> ``(polish_ops or None, target_version, beautify)``."""
    if opts.defaults:
        polish_on, polish_ops, beautify, version = False, {}, False, '3'
    else:
        polish_on, polish_ops = _saved('polish_settings', (False, {}), warnings)
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

def collect_files(paths, recursive, warnings):
    """-> the files to work on, in the order they were named. Directories expand to EPUBs."""
    found, seen = [], set()

    def add(p):
        # dedupe on the resolved path, but keep the path as it was given: echoing an absolute
        # path back at someone who typed a relative one makes the output much harder to read
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            found.append(os.path.normpath(p))

    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    for f in sorted(files):
                        if f.lower().endswith('.epub'):
                            add(os.path.join(root, f))
            else:
                for f in sorted(os.listdir(p)):
                    full = os.path.join(p, f)
                    if os.path.isfile(full) and f.lower().endswith('.epub'):
                        add(full)
        elif os.path.isfile(p):
            add(p)
        else:
            warnings.append('no such file or directory: %s' % p)
    return found


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


def process_files(paths, ctx, opts, out):
    """Work through standalone files. -> list of result dicts."""
    results = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        converting = ext != '.epub'
        if converting and not opts.convert:
            results.append(_skipped(path, 'not an EPUB (use --convert)'))
            continue

        base = os.path.splitext(os.path.basename(path))[0] + '.epub'
        if opts.output_dir:
            final = os.path.join(opts.output_dir, base)
        elif converting:
            final = os.path.normpath(os.path.join(os.path.dirname(path), base))
        else:
            final = path

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
                results.append(result)
                continue

            # Any stage may have changed the book, not only the layout fix, so the file itself
            # is the authority on whether there is something worth keeping.
            changed = converting or (os.path.exists(work) and _digest(work) != before)
            result['changed_on_disk'] = changed
            if changed:
                if opts.backup and final == path and os.path.exists(path):
                    shutil.copy2(path, path + '.bak')
                    result['backup'] = path + '.bak'
                d = os.path.dirname(final)
                if d and not os.path.isdir(d):
                    os.makedirs(d)
                shutil.move(work, final)
                result['written'] = final
            results.append(result)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return results


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
        for book_id in ids:
            fmts = {f.upper() for f in (db.formats(book_id) or ())}
            title = db.field_for('title', book_id) or str(book_id)
            src_fmt = 'EPUB' if 'EPUB' in fmts else None
            if src_fmt is None:
                if not opts.convert:
                    results.append(_skipped(title, 'no EPUB format (use --convert)',
                                            book_id=book_id))
                    continue
                src_fmt = jobs.pick_source(fmts, opts.from_format)
                if src_fmt is None:
                    results.append(_skipped(title, 'no convertible format', book_id=book_id))
                    continue
            elif opts.convert and opts.from_format:
                src_fmt = jobs.pick_source(fmts, opts.from_format)

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
                    if opts.report:
                        job['path'] = work

                result = run_one(job, opts, out)
                result['source'] = '%s (#%d)' % (title, book_id)

                if opts.report or opts.dry_run or result.get('error'):
                    results.append(result)
                    continue

                changed = converting or (os.path.exists(work) and _digest(work) != before)
                result['changed_on_disk'] = changed
                if changed:
                    try:
                        # the same call calibre's Polish action makes, so Restore original works
                        db.save_original_format(book_id, 'EPUB')
                    except Exception:                          # noqa: BLE001 - no EPUB yet
                        pass
                    with open(work, 'rb') as f:
                        db.add_format(book_id, 'EPUB', f, run_hooks=False)
                    result['written'] = 'library #%d' % book_id
                results.append(result)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        return results
    finally:
        try:
            library.close()
        except Exception:                                      # noqa: BLE001
            pass


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


def _skipped(source, reason, book_id=None):
    d = jobs._as_dict(str(source), [], None, None)
    d.update({'source': str(source), 'title': os.path.basename(str(source)),
              'skipped_reason': reason, 'book_id': book_id})
    return d


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


def report(results, opts, warnings, out):
    """Print the run. -> the exit code."""
    failed = [r for r in results if r.get('error')]
    needs = [r for r in results if r.get('changed') and not r.get('error')]

    if opts.as_json:
        json.dump({'books': results, 'warnings': warnings,
                   'failed': len(failed), 'needing_work': len(needs)},
                  sys.stdout, indent=2, default=str)
        sys.stdout.write('\n')
    else:
        for r in results:
            if r.get('skipped_reason'):
                if not opts.quiet:
                    out.write('SKIP  %s: %s\n' % (r['source'], r['skipped_reason']))
                continue
            if r.get('error'):
                out.write('FAIL  %s: %s\n' % (r['source'], r['error'].strip().splitlines()[-1]))
                continue
            if opts.quiet:
                continue
            # only worth naming the destination when it is not the file we were handed
            written = r.get('written')
            where = '' if not written or written == r['source'] else ' -> %s' % written
            if r.get('changed'):
                verb = 'would fix' if (opts.report or opts.dry_run) else 'fixed'
                out.write('  ok  %s: %s - %s%s\n' % (r['source'], verb, _describe(r), where))
            elif r.get('changed_on_disk'):
                # the layout was already sound, but converting, polishing, upgrading or
                # beautifying still rewrote the book, and that output is worth keeping
                out.write('  ok  %s: no layout changes; rewritten by %s%s\n'
                          % (r['source'], _stages(r), where))
            else:
                out.write('  ok  %s: nothing to do\n' % r['source'])
            if opts.verbose:
                for entry in r.get('ledger', ()):
                    out.write('        %-7s %-14s %s%s\n'
                              % (entry.get('action', ''), entry.get('category', ''),
                                 entry.get('page', ''),
                                 '  (%s)' % entry['reason'] if entry.get('reason') else ''))
            for problem in r.get('problems', ()):
                out.write('        !! %s\n' % problem)

        if not opts.quiet:
            noun = 'needing work' if (opts.report or opts.dry_run) else 'with layout repairs'
            out.write('\n%d book(s), %d %s, %d failed\n'
                      % (len(results), len(needs), noun, len(failed)))
            written = [r for r in results if r.get('written')]
            if written and not (opts.report or opts.dry_run):
                out.write('%d file(s) written\n' % len(written))
            if opts.dry_run:
                out.write('dry run: nothing was written\n')

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

    warnings = []
    settings = resolve_settings(opts, warnings)
    polish_ops, version, beautify = resolve_pipeline(opts, warnings)
    ctx = {'job': {
        'settings': settings,
        'polish_ops': polish_ops,
        'target_version': version,
        'beautify': beautify,
        'dry_run': bool(opts.dry_run),
    }}

    try:
        if opts.library:
            results = process_library(opts, ctx, out)
        else:
            files = collect_files(opts.paths, opts.recursive, warnings)
            if not files:
                for w in warnings:
                    out.write('warning: %s\n' % w)
                out.write('no EPUB files found\n')
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
