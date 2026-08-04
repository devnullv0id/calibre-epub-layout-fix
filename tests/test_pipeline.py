#!/usr/bin/env python3
"""End-to-end test of the convert / polish / upgrade / fix pipeline.

Must run inside calibre's interpreter, since it drives calibre's conversion and polish:

    calibre-debug tests/test_pipeline.py [source-book ...]

With no arguments it exercises the EPUB 2 -> EPUB 3 upgrade on a generated fixture, which is
the path the synthetic engine tests cannot reach.
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
FAILURES = []


def check(name, cond, msg):
    print(('  PASS  ' if cond else '  FAIL  ') + '%-30s %s' % (name, msg))
    if not cond:
        FAILURES.append('%s: %s' % (name, msg))


def epub_version(path):
    from calibre_plugins.epub_layout_fix import fixer
    return fixer.epub_version(path)


def test_upgrade(workdir):
    print('\n=== EPUB 2 -> EPUB 3 upgrade, then fix ===')
    from calibre_plugins.epub_layout_fix import jobs
    from calibre_plugins.epub_layout_fix.config import current_settings

    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    src = os.path.join(HERE, 'fixtures', 'epub2.epub')
    book = os.path.join(workdir, 'epub2.epub')
    shutil.copy(src, book)
    shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)

    check('before', (epub_version(book) or '').startswith('2'),
          'fixture starts as EPUB %s' % epub_version(book))

    res = jobs.process_book(book, current_settings(), polish_ops=None, target_version='3')
    check('pipeline', not res['error'], 'no error: %s' % (res['error'] or 'ok'))
    for step, ok, msg in res['steps']:
        print('        step %-10s %s  %s' % (step, 'ok' if ok else 'FAILED', msg[:70]))

    check('after', (epub_version(book) or '').startswith('3'),
          'upgraded to EPUB %s' % epub_version(book))

    z = zipfile.ZipFile(book)
    opf = [n for n in z.namelist() if n.endswith('.opf')][0]
    opf_text = z.read(opf).decode('utf-8', 'replace')
    check('nav', 'properties="nav"' in opf_text, 'a nav document exists after the upgrade')
    check('fix', res['image_pages'] == 1, 'the full-page image was still rewritten')
    check('svgprop', 'properties="svg"' in opf_text,
          'properties="svg" now written, since the book is EPUB 3')

    page = [n for n in z.namelist() if n.endswith('p1.xhtml')][0]
    svg = ET.fromstring(z.read(page)).find('.//{http://www.w3.org/2000/svg}svg')
    check('svgpage', svg is not None and svg.get('preserveAspectRatio') == 'xMidYMid meet',
          'the page is a correct SVG page object')

    i0 = z.infolist()[0]
    check('zip', i0.filename == 'mimetype' and i0.compress_type == 0 and z.testzip() is None,
          'mimetype first + STORED, CRCs valid')

    print('\n=== target EPUB 2 leaves the version alone ===')
    book2 = os.path.join(workdir, 'stay2.epub')
    shutil.copy(src, book2) if os.path.exists(src) else None
    if not os.path.exists(book2):
        subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                       check=True, capture_output=True, cwd=HERE)
        shutil.copy(os.path.join(HERE, 'fixtures', 'epub2.epub'), book2)
        shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)
    res2 = jobs.process_book(book2, current_settings(), polish_ops=None, target_version='2')
    check('epub2 target', (epub_version(book2) or '').startswith('2'),
          'still EPUB %s' % epub_version(book2))
    check('epub2 target', res2['image_pages'] == 1, 'image page still rewritten')
    z2 = zipfile.ZipFile(book2)
    opf2 = z2.read([n for n in z2.namelist() if n.endswith('.opf')][0]).decode('utf-8', 'replace')
    check('epub2 target', 'properties="svg"' not in opf2,
          'properties="svg" correctly NOT written for EPUB 2')


def test_convert(workdir, sources):
    print('\n=== convert -> polish -> fix on real books ===')
    from calibre_plugins.epub_layout_fix import jobs
    from calibre_plugins.epub_layout_fix.config import current_settings

    for src in sources:
        name = os.path.basename(src)
        out = os.path.join(workdir, os.path.splitext(name)[0] + '.epub')
        res = jobs.process_book(out, current_settings(),
                                polish_ops={'upgrade_book': True},
                                target_version='3', convert_from=src)
        ok = not res['error']
        check('convert', ok, '%s -> %s' % (name, res['error'] or 'ok'))
        if not ok:
            continue
        for step, sok, msg in res['steps']:
            print('        step %-10s %s  %s' % (step, 'ok' if sok else 'FAILED', msg[:70]))
        check('convert', (epub_version(out) or '').startswith('3'),
              '%s is EPUB %s' % (name, epub_version(out)))
        z = zipfile.ZipFile(out)
        check('convert', z.testzip() is None and z.infolist()[0].filename == 'mimetype',
              '%s: archive sound' % name)


def test_metadata(workdir):
    """The library's metadata must reach the converted book.

    Without a ``read_metadata_from_opf`` recommendation the output keeps whatever the source file
    had embedded, which showed up as the author appearing in sort form ("Rowling, J.K."), the
    file-as refinement reading "Unknown", multi-author order flipped, and the calibre identifier
    that ties the file back to its library record missing altogether.
    """
    print('\n=== library metadata survives the conversion ===')
    from calibre.customize.conversion import OptionRecommendation
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre.ebooks.metadata.opf2 import metadata_to_opf

    from calibre_plugins.epub_layout_fix import jobs
    from calibre_plugins.epub_layout_fix.config import current_settings

    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    src = os.path.join(workdir, 'meta-src.epub')
    shutil.copy(os.path.join(HERE, 'fixtures', 'epub2.epub'), src)
    shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)

    mi = Metadata('The Copper Gauntlet', ['Cassandra Clare', 'Holly Black'])
    mi.author_sort = 'Clare, Cassandra & Black, Holly'
    mi.title_sort = 'Copper Gauntlet, The'
    mi.uuid = '11111111-2222-3333-4444-555555555555'
    mi.set_identifiers({'mobi-asin': 'B00TEST123'})
    mi.cover = None
    mi.cover_data = (None, None)

    opf_path = os.path.join(workdir, 'meta.opf')
    data = metadata_to_opf(mi)
    with open(opf_path, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))

    out = os.path.join(workdir, 'meta-out.epub')
    res = jobs.process_book(
        out, current_settings(), polish_ops=None, target_version='3', convert_from=src,
        recommendations=[('read_metadata_from_opf', opf_path, OptionRecommendation.HIGH)])
    check('metadata', not res['error'], 'converted: %s' % (res['error'] or 'ok'))
    if res['error']:
        return

    z = zipfile.ZipFile(out)
    opf = z.read([n for n in z.namelist() if n.endswith('.opf')][0]).decode('utf-8', 'replace')
    z.close()

    creators = re.findall(r'<dc:creator[^>]*>([^<]*)</dc:creator>', opf)
    check('metadata', creators[:2] == ['Cassandra Clare', 'Holly Black'],
          'authors in display form and original order: %s' % creators)
    check('metadata', 'Clare, Cassandra &amp; Black, Holly' in opf or
                      'Clare, Cassandra & Black, Holly' in opf,
          'author sort carried across, not "Unknown"')
    check('metadata', 'Unknown' not in re.findall(r'property="file-as">([^<]*)<', opf),
          'no file-as reads "Unknown": %s' % re.findall(r'property="file-as">([^<]*)<', opf))
    check('metadata', mi.uuid in opf, 'the library uuid survives (%s)' % mi.uuid)
    check('metadata', 'B00TEST123' in opf, 'the mobi-asin identifier survives')


def test_cover_overrides(workdir):
    """The cover has to end up immune to the book's own stylesheet."""
    print('\n=== cover page overrides ===')
    from calibre_plugins.epub_layout_fix import fixer

    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    book = os.path.join(workdir, 'cover.epub')
    shutil.copy(os.path.join(HERE, 'fixtures', 'svgnone.epub'), book)
    shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)

    settings = dict(fixer.DEFAULT_SETTINGS)
    res = fixer.fix_epub(book, settings)
    check('cover', not res.error and not res.problems,
          'fixed: %s' % (res.error or res.problems or 'ok'))

    z = zipfile.ZipFile(book)
    pages = [n for n in z.namelist() if n.lower().endswith(('.xhtml', '.html'))]
    marked = [n for n in pages if fixer.COVER_MARKER in z.read(n).decode('utf-8', 'replace')]
    if not marked:
        print('        (no cover page in this fixture - nothing to check)')
        z.close()
        return
    text = z.read(marked[0]).decode('utf-8', 'replace')
    z.close()
    for want in ('width: auto !important', 'height: 100% !important', 'margin: 0 !important'):
        check('cover', want in text, 'cover carries %r' % want)
    check('cover', text.count(fixer.COVER_MARKER) == 2,
          'the block is delimited at both ends so a re-run replaces it whole')

    # and a second run must be a no-op
    again = fixer.fix_epub(book, settings)
    check('cover', not again.changed, 'a second run changes nothing (idempotent)')


def main():
    sources = [a for a in sys.argv[1:] if os.path.isfile(a)]
    workdir = tempfile.mkdtemp(prefix='eplf-pipeline-')
    try:
        test_upgrade(workdir)
        test_metadata(workdir)
        test_cover_overrides(workdir)
        if sources:
            test_convert(workdir, sources)
        else:
            print('\n=== conversion test SKIPPED (pass source books as arguments) ===')
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print('\n%d failure(s)' % len(FAILURES))
    for f in FAILURES:
        print('   !!', f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
