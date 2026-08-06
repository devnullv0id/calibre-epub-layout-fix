#!/usr/bin/env python3
"""The command line, end to end.

    calibre-debug tests/test_cli.py

Runs against the installed plugin, the same as the other suites here. Every check goes through
:func:`cli.main` rather than poking at helpers, because the interesting failures - a flag that
never reaches the settings, an output that lands in the wrong place, a book written back when it
should not have been - only show up from the outside.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []


def check(name, cond, msg):
    print(('  PASS  ' if cond else '  FAIL  ') + '%-10s %s' % (name, msg))
    if not cond:
        FAILURES.append('%s: %s' % (name, msg))


def run(args):
    """-> ``(exit_code, output)``. Never raises; a usage error is an exit code like any other."""
    from calibre_plugins.epub_layout_fix import cli
    buf = io.StringIO()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        code = cli.main(args, out=buf)
    except SystemExit as e:                                    # argparse errors exit directly
        code = e.code if isinstance(e.code, int) else 2
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
    return code, buf.getvalue()


def digest(path):
    import hashlib
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


_sha = digest


def fixtures(tmp):
    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    src = os.path.join(HERE, 'fixtures')
    dest = os.path.join(tmp, 'books')
    shutil.copytree(src, dest)
    shutil.rmtree(src, ignore_errors=True)
    return dest


# -- the checks -------------------------------------------------------------------------

def check_usage():
    print('\n=== usage errors ===')
    code, out = run([])
    check('usage', code == 2, 'no arguments is a usage error (exit %s)' % code)
    code, out = run(['--library', 'x', '--all', 'book.epub'])
    check('usage', code == 2, 'PATHs and --library together are refused')
    code, out = run(['--library', 'x'])
    check('usage', code == 2, '--library without a selection is refused')
    code, out = run(['--output-dir', 'o', '--library', 'x', '--all'])
    check('usage', code == 2, '--output-dir is refused in library mode')
    code, out = run(['--backup', '--output-dir', 'o', 'b.epub'])
    check('usage', code == 2, '--backup and --output-dir together are refused')
    code, out = run(['--library', 'x', '--all', '--backup'])
    check('usage', code == 2, '--backup is refused in library mode rather than ignored')

    # flags that cannot do anything in the mode they were given with. Accepting them silently is
    # how someone ends up believing a --dry-run wrote something to --output-dir.
    for args, why in ((['--report', '--backup', 'b.epub'], '--report --backup'),
                      (['--dry-run', '--output-dir', 'o', 'b.epub'], '--dry-run --output-dir'),
                      (['--fail-on-change', 'b.epub'], '--fail-on-change on a real run')):
        code, out = run(args)
        check('usage', code == 2, '%s is refused (got %s)' % (why, code))

    # values the engine would otherwise take at face value
    for bad in ('-1', '101', 'nan', 'inf'):
        code, out = run(['--report', '--min-width', bad, 'b.epub'])
        check('usage', code == 2, '--min-width %s is refused (got %s)' % (bad, code))
    # b.epub does not exist, so this exits 2 either way - what matters is *which* complaint
    code, out = run(['--report', '--min-width', '80', 'b.epub'])
    check('usage', 'percentage' not in out, 'but --min-width 80 draws no complaint')
    for bad in ('red', '#gggggg', '#12345', ''):
        code, out = run(['--report', '--cover-color', bad, 'b.epub'])
        check('usage', code == 2, '--cover-color %r is refused (got %s)' % (bad, code))
    code, out = run(['--report', '--cover-color', '#1a2B3c', 'b.epub'])
    check('usage', '#RRGGBB' not in out, 'but a real #RRGGBB draws none either')


def check_settings():
    print('\n=== settings resolution ===')
    from calibre_plugins.epub_layout_fix import cli, fixer

    parser = cli.build_parser()
    warnings = []

    opts = parser.parse_args(['--defaults', 'x.epub'])
    s = cli.resolve_settings(opts, warnings)
    check('settings', s == fixer.DEFAULT_SETTINGS,
          '--defaults gives exactly the built-in settings')

    opts = parser.parse_args(['--defaults', '--no-covers', '--captioned',
                              '--min-width', '55.5', '--cover-color', '#123456', 'x.epub'])
    s = cli.resolve_settings(opts, warnings)
    check('settings', s['fix_covers'] is False, '--no-covers turns covers off')
    check('settings', s['fix_captioned'] is True, '--captioned turns captioned pages on')
    check('settings', s['min_width_percent'] == 55.5, '--min-width is a number: %r'
          % s['min_width_percent'])
    check('settings', s['cover_color'] == '#123456', '--cover-color is carried through')
    check('settings', s['fix_images'] is fixer.DEFAULT_SETTINGS['fix_images'],
          'a setting nobody mentioned keeps its default')

    # the saved settings are the starting point unless --defaults says otherwise
    from calibre_plugins.epub_layout_fix.config import current_settings
    opts = parser.parse_args(['x.epub'])
    check('settings', cli.resolve_settings(opts, warnings) == current_settings(),
          'with no flags the saved settings are used unchanged')

    opts = parser.parse_args(['--defaults', '--no-polish', 'x.epub'])
    ops, version, beautify = cli.resolve_pipeline(opts, warnings)
    check('settings', ops is None, '--no-polish means no polish operations')
    opts = parser.parse_args(['--defaults', '--polish', '--epub-version', '2',
                              '--beautify', 'x.epub'])
    ops, version, beautify = cli.resolve_pipeline(opts, warnings)
    check('settings', ops, '--polish gives it something to do: %r' % (ops,))
    check('settings', version == '2', '--epub-version reaches the pipeline')
    check('settings', beautify is True, '--beautify reaches the pipeline')

    # "Embed referenced fonts" copies fonts off this machine into the book and "Download external
    # resources" fetches remote URLs. config.AUTO_POLISH_EXCLUDED already rules both out of
    # anything unattended, and a command line in a script is exactly that.
    from calibre_plugins.epub_layout_fix.config import AUTO_POLISH_EXCLUDED, prefs
    saved = dict(prefs.get('polish_ops') or {})
    try:
        prefs['polish_ops'] = {k: True for k in
                               list(AUTO_POLISH_EXCLUDED) + ['remove_unused_css']}
        prefs['polish_enabled'] = True
        ops, _v, _b = cli.resolve_pipeline(parser.parse_args(['x.epub']), warnings)
        leaked = sorted(k for k in AUTO_POLISH_EXCLUDED if (ops or {}).get(k))
        check('settings', not leaked,
              'the command line never runs %s: leaked %r'
              % ('/'.join(sorted(AUTO_POLISH_EXCLUDED)), leaked))
        check('settings', (ops or {}).get('remove_unused_css'),
              'while the rest of the saved polish operations still come through')
    finally:
        prefs['polish_ops'] = saved


def check_collect(tmp):
    print('\n=== collecting files ===')
    from calibre_plugins.epub_layout_fix import cli
    books = os.path.join(tmp, 'books')
    warnings = []

    flat = cli.collect_files([books], False, warnings)
    check('collect', flat and all(f.lower().endswith('.epub') for f in flat),
          'a directory yields its EPUBs (%d)' % len(flat))

    nested = os.path.join(books, 'deeper')
    os.makedirs(nested, exist_ok=True)
    shutil.copy(os.path.join(books, 'anchors.epub'), os.path.join(nested, 'buried.epub'))
    check('collect', len(cli.collect_files([books], False, warnings)) == len(flat),
          'without --recursive a sub-directory is left alone')
    deep = cli.collect_files([books], True, warnings)
    check('collect', any(f.endswith('buried.epub') for f in deep),
          'with --recursive it is found')
    shutil.rmtree(nested, ignore_errors=True)

    one = os.path.join(books, 'anchors.epub')
    check('collect', cli.collect_files([one, one], False, warnings) == [os.path.normpath(one)],
          'the same file named twice is processed once')

    warnings = []
    cli.collect_files([os.path.join(tmp, 'nope')], False, warnings)
    check('collect', warnings, 'a missing path is a warning, not a crash')

    # a directory scan must widen when --convert is on, or "--convert --recursive ~/Books" walks
    # straight past every AZW3 in the library
    from calibre_plugins.epub_layout_fix import jobs
    mixed = os.path.join(tmp, 'mixed')
    os.makedirs(mixed, exist_ok=True)
    shutil.copy(os.path.join(books, 'anchors.epub'), os.path.join(mixed, 'kept.epub'))
    with open(os.path.join(mixed, 'other.mobi'), 'wb') as f:
        f.write(b'not really a mobi')
    plain = cli.collect_files([mixed], False, warnings)
    check('collect', [os.path.basename(f) for f in plain] == ['kept.epub'],
          'without --convert a directory still yields EPUBs only: %r'
          % [os.path.basename(f) for f in plain])
    wide = cli.collect_files([mixed], False, warnings, jobs.SOURCE_PREFERENCE)
    check('collect', sorted(os.path.basename(f) for f in wide) == ['kept.epub', 'other.mobi'],
          'with --convert it yields the convertible formats too: %r'
          % sorted(os.path.basename(f) for f in wide))

    # and a convertible file must not be allowed to convert over the EPUB sitting next to it
    shutil.copy(os.path.join(books, 'anchors.epub'), os.path.join(mixed, 'twin.epub'))
    with open(os.path.join(mixed, 'twin.azw3'), 'wb') as f:
        f.write(b'x')
    names = [os.path.basename(f) for f in cli.collect_files([mixed], False, warnings,
                                                            jobs.SOURCE_PREFERENCE)]
    check('collect', 'twin.epub' in names and 'twin.azw3' not in names,
          'a twin.azw3 beside twin.epub is dropped: %r' % sorted(names))
    shutil.rmtree(mixed, ignore_errors=True)


def check_report(tmp):
    print('\n=== --report ===')
    book = os.path.join(tmp, 'books', 'anchors.epub')
    before = digest(book)

    code, out = run(['--report', '-v', book])
    check('report', code == 0, 'exit 0 (got %s)' % code)
    check('report', 'would fix' in out, 'says what it would fix')
    check('report', 'rewrite' in out, 'and -v shows the ledger:\n%s'
          % '\n'.join(l for l in out.splitlines() if 'rewrite' in l))
    check('report', digest(book) == before, 'the book itself is untouched')

    code, out = run(['--report', '-q', '--fail-on-change', book])
    check('report', code == 3, '--fail-on-change exits 3 when work is needed (got %s)' % code)
    check('report', out.strip() == '', '-q prints nothing for a healthy run: %r' % out)

    clean = os.path.join(tmp, 'books', 'smallimg.epub')
    code, out = run(['--report', '-q', '--fail-on-change', clean])
    check('report', code == 0, 'and 0 when there is nothing to do (got %s)' % code)

    code, out = run(['--report', '--json', book])
    try:
        data = json.loads(out[out.index('{'):])
    except Exception as e:                                     # noqa: BLE001
        data = None
        check('report', False, '--json did not parse: %s' % e)
    if data:
        check('report', data['needing_work'] == 1 and data['failed'] == 0,
              '--json carries the totals: %r' % {k: data[k] for k in ('needing_work', 'failed')})
        check('report', data['books'][0]['ledger'], 'and the per-page ledger')


def check_dry_run(tmp):
    print('\n=== --dry-run ===')
    book = os.path.join(tmp, 'books', 'cover.epub')
    before = digest(book)
    code, out = run(['--dry-run', book])
    check('dry', code == 0, 'exit 0 (got %s)' % code)
    check('dry', digest(book) == before, 'the book is byte-identical afterwards')
    check('dry', 'nothing was written' in out, 'and it says so: %r' % out.strip().splitlines()[-1])


def check_fix_in_place(tmp):
    print('\n=== fixing in place ===')
    book = os.path.join(tmp, 'books', 'dangling.epub')
    before = digest(book)

    code, out = run(['--backup', book])
    check('fix', code == 0, 'exit 0 (got %s)' % code)
    check('fix', digest(book) != before, 'the book was rewritten')
    bak = book + '.bak'
    check('fix', os.path.exists(bak) and digest(bak) == before,
          '--backup kept the original alongside it')

    # the whole point: running again finds nothing left to do
    code, out = run(['--report', book])
    check('fix', 'nothing to do' in out, 'a second look finds nothing left: %s'
          % out.strip().splitlines()[0])

    # ... and a second real run must not touch the file at all. Polish rewrites a book every time
    # it runs, so a command line keying off the bytes churns a library forever.
    settled = digest(book)
    code, out = run([book])
    check('fix', digest(book) == settled,
          'a second real run leaves the file byte-identical: %s' % out.strip().splitlines()[0])
    code, out = run([book])
    check('fix', digest(book) == settled, 'and so does a third')

    # an existing .bak is from an earlier run, so it is closer to the original than anything a
    # later one could save
    with open(bak, 'rb') as f:
        first_backup = f.read()
    fresh = os.path.join(tmp, 'books', 'pcthref2.epub')
    shutil.copy(os.path.join(tmp, 'books', 'anchors.epub'), fresh)
    run(['--backup', fresh])
    run(['--backup', fresh])
    with open(bak, 'rb') as f:
        check('fix', f.read() == first_backup, 'the .bak from the first run is left alone')

    # a stage asked for by name is meant to rewrite, so its output is kept even when the layout
    # was already sound
    plain = os.path.join(tmp, 'books', 'smallimg.epub')
    run([plain])
    settled = digest(plain)
    code, out = run(['--beautify', plain])
    check('fix', digest(plain) != settled,
          '--beautify keeps its output on a book needing no layout repair: %s'
          % out.strip().splitlines()[0])


def check_output_dir(tmp):
    print('\n=== --output-dir ===')
    book = os.path.join(tmp, 'books', 'pcthref.epub')
    before = digest(book)
    outdir = os.path.join(tmp, 'out', 'nested')          # deliberately does not exist yet

    notadir = os.path.join(tmp, 'books', 'plain-file')
    open(notadir, 'w').close()
    code, out = run(['--output-dir', notadir, book])
    check('outdir', code == 2, 'an --output-dir that is a file is refused up front (got %s)'
          % code)
    check('outdir', 'FileExistsError' not in out and 'WinError' not in out,
          'with a plain message, not an OS error: %s' % out.strip().splitlines()[-1][:90])

    code, out = run(['--output-dir', outdir, book])
    produced = os.path.join(outdir, 'pcthref.epub')
    check('outdir', code == 0, 'exit 0 (got %s)' % code)
    check('outdir', os.path.exists(produced), 'the result is written into a created directory')
    check('outdir', digest(book) == before, 'and the input is left exactly as it was')


def check_convert(tmp):
    print('\n=== --convert ===')
    src = os.path.join(tmp, 'books', 'plain.txt')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('Chapter One\n\nJust enough text to convert.\n')

    code, out = run([src])
    check('convert', 'not an EPUB' in out, 'a non-EPUB is skipped unless asked: %s'
          % out.strip().splitlines()[0])

    # --report only ever opens a zip, so a non-EPUB used to come back as "BadZipFile: File is not
    # a zip file" - a failure about the reader, saying nothing about the book
    code, out = run(['--report', '--convert', src])
    check('convert', 'BadZipFile' not in out, '--report on a non-EPUB is not a zip error: %s'
          % out.strip().splitlines()[0])
    check('convert', '--dry-run' in out, 'it points at the mode that does work')
    check('convert', code == 0, 'and it is a skip, not a failure (exit %s)' % code)

    code, out = run(['--convert', src])
    produced = os.path.join(tmp, 'books', 'plain.epub')
    check('convert', code == 0, 'exit 0 (got %s)' % code)
    check('convert', os.path.exists(produced), 'the EPUB is written next to the source')
    check('convert', os.path.exists(src), 'and the source is left in place')


def check_robustness(tmp):
    """One book's problem must cost that book, not the run."""
    print('\n=== one bad book ===')
    books = os.path.join(tmp, 'books')
    work = os.path.join(tmp, 'rough')
    os.makedirs(work, exist_ok=True)
    good_a = os.path.join(work, 'a_good.epub')
    bad = os.path.join(work, 'b_bad.epub')
    good_b = os.path.join(work, 'c_good.epub')
    shutil.copy(os.path.join(books, 'anchors.epub'), good_a)
    shutil.copy(os.path.join(books, 'anchors.epub'), good_b)
    with open(bad, 'wb') as f:
        f.write(b'this is not an epub at all')

    code, out = run([good_a, bad, good_b])
    check('rough', code == 1, 'a failed book makes the run exit 1 (got %s)' % code)
    check('rough', 'FAIL' in out, 'it is reported as a failure')
    check('rough', out.count('  ok  ') == 2, 'and the books either side still ran:\n%s' % out)
    check('rough', '2 book(s)' not in out and '3 book(s)' in out,
          'the summary counts all three: %s'
          % [l for l in out.splitlines() if 'book(s)' in l])

    # A destination claimed twice would silently lose the first result. Both copies are fresh:
    # good_a has already been repaired by the run above, and a book with nothing left to fix is
    # not written at all, so it would never claim a destination to collide with.
    same = os.path.join(work, 'sub')
    os.makedirs(same, exist_ok=True)
    first = os.path.join(work, 'clash.epub')
    second = os.path.join(same, 'clash.epub')
    shutil.copy(os.path.join(books, 'cover.epub'), first)
    shutil.copy(os.path.join(books, 'dangling.epub'), second)
    outdir = os.path.join(tmp, 'collide')
    code, out = run(['--output-dir', outdir, first, second])
    check('rough', code == 1, 'a colliding output is a failure, not a silent overwrite (exit %s)'
          % code)
    check('rough', 'would overwrite' in out, 'and says which file claimed it: %s'
          % [l for l in out.splitlines() if 'overwrite' in l])
    check('rough', len(os.listdir(outdir)) == 1, 'exactly one file was written')

    # --json has to stay parseable even when there is nothing to do
    empty = os.path.join(tmp, 'empty')
    os.makedirs(empty, exist_ok=True)
    code, out = run(['--json', empty])
    check('rough', code == 2, 'an empty directory is still exit 2 (got %s)' % code)
    try:
        data = json.loads(out[out.index('{'):])
        ok = data['books'] == [] and data['warnings']
    except Exception as e:                                     # noqa: BLE001
        ok = False
        out = str(e)
    check('rough', ok, '--json still produces a parseable envelope: %s' % out.strip()[:120])


def check_library(tmp):
    print('\n=== --library ===')
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre.library import db as open_library

    # calibre refuses a library path of 75 characters or more, and the system temp directory is
    # already most of that, so this one goes next to the user's home instead.
    root = tempfile.mkdtemp(prefix='eplfcli', dir=os.path.expanduser('~'))
    lib = os.path.join(root, 'lib')
    books = os.path.join(tmp, 'books')
    try:
        _check_library(lib, books, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _check_library(lib, books, root):
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre.library import db as open_library

    api = open_library(lib).new_api
    ids = {}
    for title, name in (('Anchors', 'anchors.epub'), ('Cover', 'cover.epub')):
        ids[title] = api.add_books(
            [(Metadata(title, ['Tester']), {'EPUB': os.path.join(books, name)})],
            add_duplicates=True)[0][0]
    empty = api.add_books([(Metadata('NoEpub', ['Tester']), {})], add_duplicates=True)[0][0]
    # a book whose only format has to be converted first
    txt = os.path.join(root, 'src.txt')
    with open(txt, 'w', encoding='utf-8') as f:
        f.write('Chapter One\n\nJust enough text.\n')
    other = api.add_books([(Metadata('TextOnly', ['Tester']), {'TXT': txt})],
                          add_duplicates=True)[0][0]
    api.backend.close()

    missing = os.path.join(root, 'not-a-library')
    code, out = run(['--library', missing, '--all'])
    check('library', code == 2, 'a path with no metadata.db is a usage error (got %s)' % code)
    check('library', not os.path.exists(missing),
          'and no library is created there by the attempt')

    code, out = run(['--library', lib, '--all', '--report'])
    check('library', code == 0, 'a report over the library runs (exit %s)' % code)
    check('library', 'no EPUB format' in out, 'a book without an EPUB is skipped, not failed')
    check('library', out.count('would fix') == 2, 'both broken books are listed:\n%s' % out)

    # the converting branch has no EPUB to analyse yet, so it must skip rather than report a
    # FileNotFoundError about a temporary path the user has never heard of
    code, out = run(['--library', lib, '--ids', str(other), '--report', '--convert'])
    check('library', code == 0 and 'FAIL' not in out,
          '--report --convert skips a book that has no EPUB yet (exit %s): %s'
          % (code, out.strip().splitlines()[0]))
    check('library', '--dry-run' in out, 'and points at the mode that does work')

    code, out = run(['--library', lib, '--search', 'title:Cover', '--report'])
    check('library', out.count('would fix') == 1, '--search narrows it to one: %s'
          % out.strip().splitlines()[0])
    code, out = run(['--library', lib, '--ids', str(ids['Anchors']), '--report'])
    check('library', 'Anchors' in out and 'Cover' not in out, '--ids selects exactly that book')

    code, out = run(['--library', lib, '--all'])
    check('library', code == 0, 'the real run completes (exit %s)' % code)

    api = open_library(lib).new_api
    try:
        fmts = {t: {f.upper() for f in api.formats(i)} for t, i in ids.items()}
        check('library', all('ORIGINAL_EPUB' in f for f in fmts.values()),
              "the original is kept so calibre's Restore original works: %r" % fmts)
        check('library', not api.formats(empty), 'the book without an EPUB gained nothing')
    finally:
        api.backend.close()

    code, out = run(['--library', lib, '--all', '--report'])
    check('library', 'would fix' not in out, 'and a second report finds nothing left:\n%s' % out)

    # The one that matters. calibre's save_original_format overwrites, so a run that writes on
    # every pass replaces ORIGINAL_EPUB with the previous pass's output until the real original
    # is gone - measured at three passes before this was fixed.
    api = open_library(lib).new_api
    pristine = {t: _sha(api.format_abspath(i, 'ORIGINAL_EPUB')) for t, i in ids.items()}
    settled = {t: _sha(api.format_abspath(i, 'EPUB')) for t, i in ids.items()}
    api.backend.close()

    for pass_no in (2, 3):
        run(['--library', lib, '--all', '-q'])
    api = open_library(lib).new_api
    try:
        now_orig = {t: _sha(api.format_abspath(i, 'ORIGINAL_EPUB')) for t, i in ids.items()}
        now_epub = {t: _sha(api.format_abspath(i, 'EPUB')) for t, i in ids.items()}
    finally:
        api.backend.close()
    check('library', now_orig == pristine,
          'two further passes leave ORIGINAL_EPUB pristine: %r'
          % {t: 'kept' if now_orig[t] == pristine[t] else 'REPLACED' for t in pristine})
    check('library', now_epub == settled,
          'and the books themselves are untouched: %r'
          % {t: 'same' if now_epub[t] == settled[t] else 'REWRITTEN' for t in settled})


def main():
    from calibre.customize.ui import initialize_plugins
    initialize_plugins()

    tmp = tempfile.mkdtemp(prefix='eplfcli')
    try:
        fixtures(tmp)
        check_usage()
        check_settings()
        check_collect(tmp)
        check_report(tmp)
        check_dry_run(tmp)
        check_fix_in_place(tmp)
        check_output_dir(tmp)
        check_convert(tmp)
        check_robustness(tmp)
        check_library(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%d failure(s)' % len(FAILURES))
    for f in FAILURES:
        print('   !!', f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
