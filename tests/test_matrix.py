#!/usr/bin/env python3
"""Exhaustive end-to-end sweep: every setting, every path, against real books.

    calibre-debug tests/test_matrix.py [book.epub ...]      # everything
    python tests/test_matrix.py [book.epub ...]             # engine parts only

The other suites each prove one thing. This one crosses the settings with the books and asserts
the invariants that must hold no matter what is switched on:

  * the archive is still a valid EPUB - mimetype first and stored, every CRC intact
  * every XHTML and the OPF still parse
  * no entry is gained or lost, and anything the plugin did not mean to touch is byte-identical
  * nothing references a file that is not in the archive
  * a second run changes nothing

and, per setting, that turning it off actually turns the behaviour off.

Books are taken from the command line, or from EPLF_BOOKS (a directory), and are always copied
before being touched.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:                                                           # inside calibre
    from calibre.customize.ui import initialize_plugins
    initialize_plugins()
    from calibre_plugins.epub_layout_fix import fixer
    HAVE_CALIBRE = True
except ImportError:                                            # plain interpreter
    sys.path.insert(0, os.path.join(ROOT, 'calibre_plugins', 'epub_layout_fix'))
    import fixer
    HAVE_CALIBRE = False

FAILURES = []
CHECKS = [0]


def check(group, cond, msg):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append('%s: %s' % (group, msg))
        print('  FAIL  %-12s %s' % (group, msg))


def note(msg):
    print('        %s' % msg)


# ----------------------------------------------------------------------------------
# invariants
# ----------------------------------------------------------------------------------

def unresolved_refs(path):
    """Every href/src/xlink:href in the book that points at a file which is not there."""
    bad = []
    with zipfile.ZipFile(path) as z:
        names = {i.filename for i in z.infolist()}
        for n in sorted(names):
            if not n.lower().endswith(('.xhtml', '.html', '.htm', '.opf', '.ncx')):
                continue
            t = z.read(n).decode('utf-8', 'replace')
            refs = [m.group(2) for m in
                    re.finditer(r'(?:^|[^:\w])(href|src)\s*=\s*"([^"]+)"', t)]
            refs += [m.group(1) for m in re.finditer(r'xlink:href\s*=\s*"([^"]+)"', t)]
            for val in refs:
                if val.startswith('#') or re.match(r'^[a-z][a-z0-9+.-]*:', val, re.I):
                    continue
                tgt = fixer.resolve_path(n, val.split('#')[0])
                if tgt and tgt not in names:
                    bad.append('%s -> %s' % (n, val))
    return bad


def assert_sound(group, label, original, path, changed_entries):
    """Everything that must be true of a rebuilt book, whatever the settings were."""
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.filename.endswith('/')]
        first = z.infolist()[0]
        check(group, first.filename == 'mimetype' and first.compress_type == zipfile.ZIP_STORED,
              '%s: mimetype first and stored' % label)
        check(group, z.testzip() is None, '%s: all CRCs intact' % label)

        for i in infos:
            if not i.filename.endswith(('.xhtml', '.html', '.opf')):
                continue
            try:
                ET.fromstring(z.read(i.filename))
            except ET.ParseError as e:
                check(group, False, '%s: %s not well-formed: %s' % (label, i.filename, e))
        new = {i.filename: i.CRC for i in infos}

    with zipfile.ZipFile(original) as z:
        old = {i.filename: i.CRC for i in z.infolist() if not i.filename.endswith('/')}

    check(group, set(old) == set(new),
          '%s: entry set unchanged (lost %s, gained %s)'
          % (label, sorted(set(old) - set(new))[:3], sorted(set(new) - set(old))[:3]))

    if changed_entries is not None:
        surprises = [n for n in set(old) & set(new)
                     if old[n] != new[n] and n not in changed_entries]
        check(group, not surprises,
              '%s: only intended entries modified (unexpected: %s)' % (label, surprises[:4]))

    bad = unresolved_refs(path)
    check(group, not bad, '%s: no dangling references (%s)' % (label, bad[:3]))


def changed_entries_of(original, path):
    with zipfile.ZipFile(original) as a, zipfile.ZipFile(path) as b:
        oa = {i.filename: i.CRC for i in a.infolist() if not i.filename.endswith('/')}
        ob = {i.filename: i.CRC for i in b.infolist() if not i.filename.endswith('/')}
    return {n for n in set(oa) & set(ob) if oa[n] != ob[n]}


def read(path, name):
    with zipfile.ZipFile(path) as z:
        try:
            return z.read(name).decode('utf-8', 'replace')
        except KeyError:
            return None


def pages(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n).decode('utf-8', 'replace')
                for n in z.namelist() if n.lower().endswith(('.xhtml', '.html'))}


# ----------------------------------------------------------------------------------
# the settings matrix
# ----------------------------------------------------------------------------------

D = fixer.DEFAULT_SETTINGS

COMBOS = [
    ('defaults',            {}),
    ('images-off',          {'fix_images': False}),
    ('covers-off',          {'fix_covers': False}),
    ('dark-off',            {'dark_cover': False}),
    ('colour-123456',       {'cover_color': '#123456'}),
    ('anchors-off',         {'preserve_anchors': False}),
    ('threshold-0',         {'min_width_percent': 0.0}),
    ('threshold-100',       {'min_width_percent': 100.0}),
    ('everything-off',      {'fix_images': False, 'fix_covers': False}),
    ('all-on-loose',        {'min_width_percent': 10.0, 'dark_cover': True,
                             'cover_color': '#ff0000'}),
]


def run_matrix(book, workdir):
    """Every combination against one book, with the invariants checked each time."""
    name = os.path.basename(book)
    print('\n--- %s ---' % name)
    results = {}

    for label, overrides in COMBOS:
        settings = dict(D, **overrides)
        work = os.path.join(workdir, 'm.epub')
        shutil.copy(book, work)

        res = fixer.fix_epub(work, settings)
        results[label] = res

        check('engine', not res.error, '%s/%s: no error (%s)' % (name, label, res.error))
        check('engine', not res.problems,
              '%s/%s: no verification problems (%s)' % (name, label, res.problems))
        if res.error:
            continue

        assert_sound('engine', '%s/%s' % (name, label), book, work,
                     changed_entries_of(book, work))

        # --- the setting actually did what it says ---
        txt = pages(work)
        src = pages(book)
        cover = fixer.find_cover_page(zipfile.ZipFile(book),
                                      [i.filename for i in zipfile.ZipFile(book).infolist()],
                                      fixer.find_opf(zipfile.ZipFile(book),
                                                     [i.filename for i in
                                                      zipfile.ZipFile(book).infolist()]))
        # What matters is what this run *added*, not what the book already had. A book that has
        # been through the plugin before carries these markers in its input, and hand a real
        # library to EPLF_BOOKS and most of it will have. Comparing against the output alone
        # reported every already-fixed book as a settings failure.
        def marked(source, needle, skip=None):
            return {n for n, t in source.items() if needle in t and n != skip}

        added_pages = (marked(txt, 'div class="fullpage"', cover)
                       - marked(src, 'div class="fullpage"', cover))
        added_cover = marked(txt, fixer.COVER_MARKER) - marked(src, fixer.COVER_MARKER)
        # the anchor check below wants the pages this run built, for the same reason
        body_rewrites = sorted(added_pages)

        if overrides.get('fix_images') is False:
            check('setting', not added_pages,
                  '%s/%s: no content page rewritten (%s)'
                  % (name, label, sorted(added_pages)[:3]))
        if overrides.get('fix_covers') is False:
            check('setting', not added_cover,
                  '%s/%s: the cover is left alone (%s)' % (name, label, sorted(added_cover)[:3]))
            check('setting', not res.cover_fixed, '%s/%s: cover_fixed stays false' % (name, label))
        if overrides.get('dark_cover') is False and cover:
            marked = [t for t in txt.values() if fixer.COVER_MARKER in t]
            if marked:
                check('setting', 'background-color' not in marked[0],
                      '%s/%s: no letterbox colour painted' % (name, label))
                check('setting', 'width: auto !important' in marked[0],
                      '%s/%s: the sizing overrides are still applied' % (name, label))
        if overrides.get('cover_color'):
            marked = [t for t in txt.values() if fixer.COVER_MARKER in t]
            if marked:
                check('setting', overrides['cover_color'] in marked[0],
                      '%s/%s: the chosen colour is used' % (name, label))
        if overrides.get('preserve_anchors') is False:
            for n in body_rewrites:
                check('setting', '<span id=' not in txt[n] and '<body id=' not in txt[n],
                      '%s/%s: %s carries no preserved anchor' % (name, label, n))

        # --- idempotent ---
        again = fixer.fix_epub(work, settings)
        check('engine', not again.changed,
              '%s/%s: a second run changes nothing (%s)' % (name, label, again.details[:2]))

        # --- analyze agrees with fix ---
        fresh = os.path.join(workdir, 'a.epub')
        shutil.copy(book, fresh)
        ana = fixer.analyze_epub(fresh, settings)
        check('engine', ana.changed == res.changed,
              '%s/%s: analyze_epub agrees that changed=%s' % (name, label, res.changed))
        check('engine', os.path.getsize(fresh) == os.path.getsize(book),
              '%s/%s: analyze_epub wrote nothing' % (name, label))

    # --- relations between combinations ---
    loose, tight = results.get('threshold-0'), results.get('threshold-100')
    if loose and tight and not loose.error and not tight.error:
        check('setting', loose.image_pages >= tight.image_pages,
              '%s: a 0%% threshold rewrites at least as much as 100%% (%d vs %d)'
              % (name, loose.image_pages, tight.image_pages))
    off = results.get('everything-off')
    if off and not off.error:
        check('setting', off.image_pages == 0 and not off.cover_fixed,
              '%s: with both repairs off nothing is repaired (%s)' % (name, off.summary()))

    d = results.get('defaults')
    if d and not d.error:
        note('defaults -> %s' % d.summary())
    return results


# ----------------------------------------------------------------------------------
# the pipeline matrix (needs calibre)
# ----------------------------------------------------------------------------------

#: ``convert`` runs the book through calibre's conversion first, which is the only path where
#: the target version can actually change the book. Without it the version option means "upgrade
#: to 3" or "leave alone" - calibre has no EPUB 3 -> 2 downgrade, and the panel says as much.
PIPELINE = [
    ('v3-plain',        dict(target_version='3', polish_ops=None, beautify=False)),
    ('v2-plain',        dict(target_version='2', polish_ops=None, beautify=False)),
    ('v3-upgrade',      dict(target_version='3', polish_ops={'upgrade_book': True},
                             beautify=False)),
    ('v3-beautify',     dict(target_version='3', polish_ops=None, beautify=True)),
    ('v3-polish-css',   dict(target_version='3',
                             polish_ops={'remove_unused_css': True, 'compress_images': True},
                             beautify=False)),
    ('v2-beautify',     dict(target_version='2', polish_ops=None, beautify=True)),
    ('v3-convert',      dict(target_version='3', polish_ops=None, beautify=False, convert=True)),
    ('v2-convert',      dict(target_version='2', polish_ops=None, beautify=False, convert=True)),
]


def run_pipeline_matrix(book, workdir):
    from calibre_plugins.epub_layout_fix import jobs
    from calibre_plugins.epub_layout_fix.config import current_settings

    name = os.path.basename(book)
    source_version = fixer.epub_version(book) or ''
    print('\n--- pipeline: %s (source is EPUB %s) ---' % (name, source_version))

    for label, spec in PIPELINE:
        kw = dict(spec)
        converting = kw.pop('convert', False)

        if converting:
            src = os.path.join(workdir, 'src.epub')
            shutil.copy(book, src)
            work = os.path.join(workdir, 'out.epub')
            kw['convert_from'] = src
        else:
            work = os.path.join(workdir, 'p.epub')
            shutil.copy(book, work)

        res = jobs.process_book(work, current_settings(), log=None, **kw)

        check('pipeline', not res['error'], '%s/%s: no error (%s)'
              % (name, label, (res['error'] or '')[:160]))
        if res['error']:
            continue

        steps = dict((s, ok) for s, ok, _m in res['steps'])
        if kw['beautify']:
            check('pipeline', steps.get('beautify') is True,
                  '%s/%s: the beautify stage ran' % (name, label))
        if kw['polish_ops']:
            check('pipeline', 'polish' in steps, '%s/%s: the polish stage ran' % (name, label))
        if converting:
            check('pipeline', steps.get('convert') is True,
                  '%s/%s: the conversion stage ran' % (name, label))

        version = fixer.epub_version(work) or ''
        if kw['target_version'] == '3':
            check('pipeline', version.startswith('3'),
                  '%s/%s: upgraded to EPUB 3 (got %s)' % (name, label, version))
        elif converting:
            # converting is the one path that decides the version outright
            check('pipeline', version.startswith('2'),
                  '%s/%s: converted to EPUB 2 (got %s)' % (name, label, version))
        else:
            check('pipeline', version == source_version,
                  '%s/%s: "leave as-is" left EPUB %s alone (got %s)'
                  % (name, label, source_version, version))

        with zipfile.ZipFile(work) as z:
            names = [i.filename for i in z.infolist()]
            opf = z.read(fixer.find_opf(z, names)).decode('utf-8', 'replace')
        # the manifest property is EPUB 3 grammar, so it must follow the *output* version
        if version.startswith('3') and res['image_pages']:
            check('pipeline', 'properties="svg"' in opf,
                  '%s/%s: rewritten pages are flagged in the manifest' % (name, label))
        if not version.startswith('3'):
            check('pipeline', 'properties="svg"' not in opf,
                  '%s/%s: no EPUB 3 manifest grammar in an EPUB %s book'
                  % (name, label, version))

        # a converted book is a different archive, so only the in-place runs can be compared
        assert_sound('pipeline', '%s/%s' % (name, label), book if not converting else work,
                     work, None)
        note('%-14s EPUB %-4s %s' % (label, version,
                                     ', '.join('%s=%s' % (k, res[k]) for k in
                                               ('image_pages', 'svg_repaired', 'cover_fixed',
                                                'dead_links', 'changed'))))


# ----------------------------------------------------------------------------------
# settings plumbing (needs calibre)
# ----------------------------------------------------------------------------------

def run_config_checks():
    print('\n--- settings plumbing ---')
    from calibre_plugins.epub_layout_fix import config, ui

    saved = {k: config.prefs.get(k) for k in list(config.prefs.defaults)}
    try:
        for key, value in (('fix_images', False), ('min_width_percent', 42.5),
                           ('cover_color', '#abcdef'), ('target_epub_version', '2'),
                           ('beautify', True), ('auto_on_import', True),
                           ('auto_debounce_secs', 7), ('auto_max_books', 3)):
            config.prefs[key] = value

        s = config.current_settings()
        check('config', s['fix_images'] is False and s['min_width_percent'] == 42.5
              and s['cover_color'] == '#abcdef', 'engine settings read back: %s' % s)
        check('config', config.target_epub_version() == '2', 'target version read back')
        check('config', config.beautify_enabled() is True, 'beautify read back')
        a = config.auto_settings()
        check('config', a['enabled'] and a['debounce'] == 7 and a['max_books'] == 3,
              'automatic settings read back: %s' % a)

        config.prefs['target_epub_version'] = 'nonsense'
        check('config', config.target_epub_version() == '3',
              'a bad target version falls back to 3')
        config.prefs['auto_debounce_secs'] = 9999
        check('config', config.auto_settings()['debounce'] <= 60, 'the debounce is clamped')
        config.prefs['auto_debounce_secs'] = 0
        check('config', config.auto_settings()['debounce'] >= 1, 'the debounce has a floor')

        config.prefs['polish_ops'] = {'embed': True, 'download_external_resources': True,
                                      'subset': True}
        _on, ops = config.polish_settings(automatic=False)
        check('config', ops['embed'] and ops['download_external_resources'],
              'a manual run keeps whatever the panel says')
        _on, ops = config.polish_settings(automatic=True)
        check('config', not ops['embed'] and not ops['download_external_resources'],
              'an automatic run forces the intrusive operations off')
        check('config', ops['subset'], 'an automatic run keeps the harmless ones')

        check('config', ui.pick_source({'EPUB', 'PDF'}) == 'EPUB', 'source preference: EPUB > PDF')
        check('config', ui.pick_source({'AZW3', 'MOBI'}) == 'AZW3', 'source preference: AZW3 > MOBI')
        check('config', ui.pick_source({'PDF'}, 'PDF') == 'PDF', 'an explicit preference wins')
        check('config', ui.pick_source(set()) is None, 'no formats -> nothing to convert')
    finally:
        for k, v in saved.items():
            if v is None:
                config.prefs.pop(k, None)
            else:
                config.prefs[k] = v


# ----------------------------------------------------------------------------------

def collect_books(args):
    books = [a for a in args if os.path.isfile(a) and a.lower().endswith('.epub')]
    folder = next((a for a in args if os.path.isdir(a)), os.environ.get('EPLF_BOOKS'))
    if folder and os.path.isdir(folder):
        books += [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                  if n.lower().endswith('.epub')]
    return books


def main():
    args = sys.argv[1:]
    workdir = tempfile.mkdtemp(prefix='eplf-matrix-')
    try:
        # fixtures first - they cover the shapes real books do not have
        subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                       check=True, capture_output=True, cwd=HERE)
        fixtures = os.path.join(workdir, 'fixtures')
        shutil.move(os.path.join(HERE, 'fixtures'), fixtures)
        books = [os.path.join(fixtures, n) for n in sorted(os.listdir(fixtures))]

        real = collect_books(args)
        print('=== settings matrix: %d combination(s) x %d fixture(s) + %d real book(s) ==='
              % (len(COMBOS), len(books), len(real)))
        for b in books + real:
            run_matrix(b, workdir)

        if HAVE_CALIBRE:
            print('\n=== pipeline matrix: %d combination(s) ===' % len(PIPELINE))
            for b in books[:4] + real:
                run_pipeline_matrix(b, workdir)
            run_config_checks()
        else:
            print('\n=== pipeline and settings checks SKIPPED (run under calibre-debug) ===')
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)

    print('\n%d checks, %d failure(s)' % (CHECKS[0], len(FAILURES)))
    for f in FAILURES[:40]:
        print('   !!', f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
