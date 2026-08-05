#!/usr/bin/env python3
"""Headless tests for the engine.

Run with a plain interpreter - the engine imports nothing from calibre or Qt:

    python tests/test_fixer.py

Three groups:
  * fixtures   - synthetic EPUBs covering cases the real library does not contain
  * parity     - the same 21-book library the PowerShell script was validated against,
                 asserting identical numbers. A port that differs is a bug.
  * idempotency- a second run must change nothing
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'calibre_plugins', 'epub_layout_fix'))

import fixer  # noqa: E402

SVG = '{http://www.w3.org/2000/svg}'
XLINK = '{http://www.w3.org/1999/xlink}'

FAILURES = []
CHECKS = [0]


def check(name, cond, msg):
    CHECKS[0] += 1
    print(('  PASS  ' if cond else '  FAIL  ') + '%-22s %s' % (name, msg))
    if not cond:
        FAILURES.append('%s: %s' % (name, msg))


def page(z, n):
    return z.read(n).decode('utf-8')


# ----------------------------------------------------------------------------------
def test_fixtures(workdir):
    print('\n=== fixtures ===')
    out = os.path.join(workdir, 'fixtures')
    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    shutil.move(os.path.join(HERE, 'fixtures'), out)

    for f in sorted(os.listdir(out)):
        if f.endswith('.epub'):
            fixer.fix_epub(os.path.join(out, f))

    z = zipfile.ZipFile(os.path.join(out, 'epub2.epub'))
    opf = page(z, 'content.opf')
    check('epub2', '<svg' in page(z, 'p1.xhtml'), 'page rewritten as SVG page object')
    check('epub2', 'properties="svg"' not in opf, 'properties=svg NOT added to an EPUB 2 OPF')

    z = zipfile.ZipFile(os.path.join(out, 'pcthref.epub'))
    check('pcthref', 'properties="svg"' in page(z, 'content.opf'),
          'properties=svg added via resolved href match')

    z = zipfile.ZipFile(os.path.join(out, 'webpsvg.epub'))
    vb1 = ET.fromstring(page(z, 'p1.xhtml')).find('.//' + SVG + 'svg').get('viewBox')
    vb2 = ET.fromstring(page(z, 'p2.xhtml')).find('.//' + SVG + 'svg').get('viewBox')
    check('webpsvg', vb1 == '0 0 1000 1500', 'WebP dimensions read -> %r' % vb1)
    check('webpsvg', vb2 == '0 0 900 1400', 'SVG image dimensions read -> %r' % vb2)

    z = zipfile.ZipFile(os.path.join(out, 'badimg.epub'))
    check('badimg', '<svg' not in page(z, 'p1.xhtml'), 'unreadable image left untouched')

    z = zipfile.ZipFile(os.path.join(out, 'svgnone.epub'))
    t = page(z, 'p1.xhtml')
    sv = ET.fromstring(t).find('.//' + SVG + 'svg')
    check('svgnone', sv.get('preserveAspectRatio') == 'xMidYMid meet', 'stretched SVG repaired')
    check('svgnone', sv.get('viewBox') == '0 0 1200 1800', 'viewBox preserved')
    check('svgnone', 'div.fullpage' not in t, 'repaired in place, not rewritten')

    for name in ('twoimg', 'caption', 'smallimg'):
        z = zipfile.ZipFile(os.path.join(out, name + '.epub'))
        check(name, '<svg' not in page(z, 'p1.xhtml'), 'left untouched by default')

    # ---- the two shapes that are only rewritten when asked for ----
    src = os.path.join(workdir, 'fixtures-src')
    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    shutil.move(os.path.join(HERE, 'fixtures'), src)

    work = os.path.join(workdir, 'cap.epub')
    shutil.copy(os.path.join(src, 'caption.epub'), work)
    res = fixer.fix_epub(work, dict(fixer.DEFAULT_SETTINGS, fix_captioned=True))
    check('captioned', res.changed and not res.problems,
          'rewritten when allowed: %s' % (res.problems or 'ok'))
    z = zipfile.ZipFile(work)
    t = page(z, 'p1.xhtml')
    z.close()
    check('captioned', '<svg' in t, 'the image became an SVG page object')
    check('captioned', '<h1>READ THE FIRST CHAPTER OF</h1>' in t,
          'the caption keeps its own markup, not just its text')
    check('captioned', 'html:h1' not in t and 'ns0:' not in t,
          'no namespace prefix leaks into the caption')
    check('captioned', 'height: 85.0%' in t,
          'the image gives up %d%% of the page to the caption' % fixer.CAPTION_SHARE)

    work = os.path.join(workdir, 'multi.epub')
    shutil.copy(os.path.join(src, 'twoimg.epub'), work)
    res = fixer.fix_epub(work, dict(fixer.DEFAULT_SETTINGS, fix_multi_image=True))
    check('multi', res.changed and not res.problems,
          'rewritten when allowed: %s' % (res.problems or 'ok'))
    z = zipfile.ZipFile(work)
    t = page(z, 'p1.xhtml')
    z.close()
    check('multi', t.count('<svg') == 2, 'both images kept (%d svg blocks)' % t.count('<svg'))
    check('multi', 'viewBox="0 0 1200 1800"' in t and 'viewBox="0 0 600 900"' in t,
          'each image keeps its own dimensions')
    check('multi', 'height: 50.0%' in t, 'they share the page height')

    # one full-width image beside a narrow one: stacking would blow the small one up, so the
    # whole page has to be left alone
    work = os.path.join(workdir, 'multi-narrow.epub')
    shutil.copy(os.path.join(src, 'mixedwidth.epub'), work)
    res = fixer.fix_epub(work, dict(fixer.DEFAULT_SETTINGS, fix_multi_image=True))
    narrow = [e for e in res.ledger if e['category'] == 'too-narrow']
    check('multi', not res.image_pages and narrow,
          'a page is skipped whole if any image fails the threshold: %s' % res.ledger)
    check('multi', '1 of 2 images below the threshold' in (narrow[0]['reason'] if narrow else ''),
          'and the reason says which: %s' % (narrow[0]['reason'] if narrow else '-'))
    shutil.rmtree(src, ignore_errors=True)

    z = zipfile.ZipFile(os.path.join(out, 'anchors.epub'))
    t = page(z, 'p1.xhtml')
    for a in ('wrap', 'page_42', 'theimg'):
        check('anchors', 'id="%s"' % a in t, 'anchor id %r preserved' % a)
    check('anchors', '<svg' in t, 'page rewritten')

    # ---- the cover has to survive the book's own stylesheet ----
    z = zipfile.ZipFile(os.path.join(out, 'cover.epub'))
    t = page(z, 'c0.xhtml')
    sv = ET.fromstring(t).find('.//' + SVG + 'svg')
    check('cover', sv.get('preserveAspectRatio') == 'xMidYMid meet',
          'stretched cover repaired')
    for want in ('width: auto !important', 'height: 100% !important', 'margin: 0 !important'):
        check('cover', want in t, 'overrides .calibre2 with %r' % want)
    check('cover', '@page { margin: 0' in t, 'the 5pt page margin is cancelled')
    check('cover', t.count(fixer.COVER_MARKER) == 2, 'the injected block is delimited at both ends')

    # ---- alt text becomes the SVG's accessible name ----
    t = page(z, 'p1.xhtml')
    check('alt', 'role="img"' in t, 'rewritten page is exposed as an image')
    check('alt', '<title id="%s">A map of the world</title>' % fixer.SVG_TITLE_ID in t,
          'alt text carried across as the accessible name')

    # ---- navigation links to pages that are not there ----
    z = zipfile.ZipFile(os.path.join(out, 'dangling.epub'))
    t = page(z, 'nav.xhtml')
    check('nav', 'c0.xhtml' not in t, 'the dangling link to the deleted cover page is gone')
    check('nav', '<span>Cover</span>' in t, 'the TOC entry survives as unlinked text')
    landmarks = t.split('epub:type="landmarks"')[1]
    check('nav', 'epub:type="cover"' not in landmarks and 'bodymatter' in landmarks,
          'the landmarks entry is dropped whole, the valid one is kept')
    check('nav', 'p1.xhtml' in t, 'links that do resolve are untouched')
    # calibre also leaves <link>s to stylesheets it trimmed from the book
    check('nav', 'page_styles1.css' not in t, 'the dead stylesheet link is removed')
    check('nav', 'href="style.css"' in t, 'the stylesheet that does exist is kept')

    for f in sorted(os.listdir(out)):
        if not f.endswith('.epub'):
            continue
        z = zipfile.ZipFile(os.path.join(out, f))
        i0 = z.infolist()[0]
        check('zip', i0.filename == 'mimetype' and i0.compress_type == 0 and z.testzip() is None,
              '%s: mimetype first + STORED, CRCs valid' % f)
        for n in z.namelist():
            if n.endswith(('.xhtml', '.opf')):
                try:
                    ET.fromstring(z.read(n))
                except ET.ParseError as e:
                    check('xml', False, '%s/%s not well-formed: %s' % (f, n, e))


# ----------------------------------------------------------------------------------
#: Ground truth from the verified PowerShell run over the same 21 books.
PARITY_EXPECTED = {
    'image_pages': 56,
    'svg_repaired': 0,
    'covers': 21,
    'ledger': {
        'has-text': 1090,
        'full-page-image': 56,
        'captioned-candidate': 11,
        'too-narrow': 3,
        'already-svg-ok': 2,
        'multi-image': 1,
    },
}


def test_parity(workdir, reference):
    print('\n=== parity with the PowerShell engine (21-book library) ===')
    lib = os.path.join(workdir, 'lib')
    shutil.copytree(reference, lib)

    books = []
    for dp, _dn, fn in os.walk(lib):
        for f in fn:
            if f.lower().endswith(('.epub', '.kepub')):
                books.append(os.path.join(dp, f))
    books.sort()
    check('parity', len(books) == 21, 'found %d books' % len(books))

    totals = {'image_pages': 0, 'svg_repaired': 0, 'covers': 0}
    ledger = {}
    for b in books:
        r = fixer.fix_epub(b)
        if r.error:
            check('parity', False, '%s errored: %s' % (os.path.basename(b), r.error))
            continue
        if r.problems:
            check('parity', False, '%s failed verification: %s'
                  % (os.path.basename(b), r.problems[0]))
        totals['image_pages'] += r.image_pages
        totals['svg_repaired'] += r.svg_repaired
        totals['covers'] += 1 if r.cover_fixed else 0
        for e in r.ledger:
            ledger[e['category']] = ledger.get(e['category'], 0) + 1

    for k, want in (('image_pages', PARITY_EXPECTED['image_pages']),
                    ('svg_repaired', PARITY_EXPECTED['svg_repaired']),
                    ('covers', PARITY_EXPECTED['covers'])):
        check('parity', totals[k] == want, '%s: %d (expected %d)' % (k, totals[k], want))

    for cat, want in PARITY_EXPECTED['ledger'].items():
        got = ledger.get(cat, 0)
        check('parity', got == want, 'ledger %-20s %5d (expected %d)' % (cat, got, want))
    extra = set(ledger) - set(PARITY_EXPECTED['ledger'])
    check('parity', not extra, 'no unexpected ledger categories%s'
          % ('' if not extra else ' -> %s' % sorted(extra)))

    print('\n=== idempotency ===')
    second = {'image_pages': 0, 'svg_repaired': 0, 'covers': 0, 'changed': 0}
    for b in books:
        r = fixer.fix_epub(b)
        second['image_pages'] += r.image_pages
        second['svg_repaired'] += r.svg_repaired
        second['covers'] += 1 if r.cover_fixed else 0
        second['changed'] += 1 if r.changed else 0
    check('idempotent', second['image_pages'] == 0, 'no image pages rewritten on second run')
    check('idempotent', second['svg_repaired'] == 0, 'no svg repairs on second run')
    check('idempotent', second['covers'] == 0, 'no covers touched on second run')
    check('idempotent', second['changed'] == 0, 'no book reported as changed on second run')


# ----------------------------------------------------------------------------------
def main():
    reference = None
    if len(sys.argv) > 1:
        reference = sys.argv[1]
    elif os.path.isdir(os.environ.get('EPLF_REFERENCE_LIB', '')):
        reference = os.environ['EPLF_REFERENCE_LIB']

    workdir = tempfile.mkdtemp(prefix='eplf-tests-')
    try:
        test_fixtures(workdir)
        if reference and os.path.isdir(reference):
            test_parity(workdir, reference)
        else:
            print('\n=== parity SKIPPED ===')
            print('  pass the 21-book reference library as argv[1] or set EPLF_REFERENCE_LIB')
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print('\n%d checks, %d failures' % (CHECKS[0], len(FAILURES)))
    for f in FAILURES:
        print('   !!', f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
