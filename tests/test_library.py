#!/usr/bin/env python3
"""Library-level test: the calls the toolbar actions make against a real calibre library.

    calibre-debug tests/test_library.py

Creates a throwaway library, adds a book with a deliberately broken EPUB, runs the same sequence
the GUI action does, and checks the outcome - including that calibre's own Restore original works
afterwards. No existing library is touched.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []


def check(name, cond, msg):
    print(('  PASS  ' if cond else '  FAIL  ') + '%-24s %s' % (name, msg))
    if not cond:
        FAILURES.append('%s: %s' % (name, msg))


def make_broken_epub(dest):
    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    shutil.copy(os.path.join(HERE, 'fixtures', 'anchors.epub'), dest)
    shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)
    return dest


def check_convert_dialog(legacy_db, book_id):
    """Build the real conversion window with our panels and drive its category list.

    Selecting a category must actually change the visible pane - replacing the group model
    hands the view a new selection model, which silently orphans calibre's own connection.
    """
    from calibre_plugins.epub_layout_fix.dialog import make_convert_dialog
    from calibre_plugins.epub_layout_fix.panel import LayoutFixWidget, PolishWidget

    try:
        from qt.core import QApplication, QWidget
    except ImportError:
        from PyQt5.Qt import QApplication, QWidget
    # calibre's own Application subclass, not a bare QApplication: the metadata panel reaches for
    # Application.is_dark_theme, and a plain QApplication takes the whole process down.
    from calibre.gui2 import Application
    QApplication.instance() or Application(sys.argv[:1])

    # Config expects the legacy database wrapper, not the new_api Cache. The parent must be held
    # in a local - a temporary QWidget is collected and takes the dialog's children with it.
    parent = QWidget()
    d = make_convert_dialog(parent, legacy_db, book_id)
    titles = [w.TITLE for w in d.widgets]
    check('dialog', 'Layout fixes' in titles, 'Layout fixes panel present (%d panels)' % len(titles))
    check('dialog', 'Polish' in titles, 'Polish panel present')
    check('dialog', sum(isinstance(w, (LayoutFixWidget, PolishWidget)) for w in d.widgets) == 2,
          'exactly one of each plugin panel (no duplicates)')

    fmts = [d.output_formats.itemText(i) for i in range(d.output_formats.count())]
    check('dialog', fmts == ['EPUB'], 'output format restricted to EPUB (got %r)' % fmts)
    check('dialog', not d.output_formats.isEnabled(), 'output format combo disabled')

    # every category must swap the pane in
    seen = []
    for row in range(d._groups_model.rowCount()):
        d.groups.setCurrentIndex(d._groups_model.index(row))
        seen.append(d.scrollArea.widget() is d.widgets[row])
    check('dialog', all(seen), 'selecting each category shows its pane (%d/%d correct)'
          % (sum(seen), len(seen)))

    # Config.accept() does recommendations.update(widget.commit(...)) against a dict, so every
    # panel must return a mapping. Returning a bool raises "'bool' object is not iterable" and
    # the OK button simply fails.
    for w in d.widgets:
        got = w.commit(save_defaults=False)
        check('dialog', hasattr(got, 'keys'),
              '%s.commit() returns a mapping, not %s' % (w.TITLE.replace('\n', ' '),
                                                         type(got).__name__))

    # and the real thing: accepting the dialog must not raise
    try:
        d.accept()
        check('dialog', True, 'accept() completes without raising')
    except Exception as e:                                     # noqa: BLE001
        check('dialog', False, 'accept() raised %s: %s' % (type(e).__name__, e))

    # re-running setup_pipeline (what the input-format combo does) must not duplicate panels
    d.setup_pipeline()
    n = sum(isinstance(w, (LayoutFixWidget, PolishWidget)) for w in d.widgets)
    check('dialog', n == 2, 'panels not duplicated after setup_pipeline re-runs (found %d)' % n)
    d.groups.setCurrentIndex(d._groups_model.index(0))
    check('dialog', d.scrollArea.widget() is d.widgets[0],
          'pane switching still works after the rebuild')
    d.break_cycles()


def main():
    from calibre.customize.ui import initialize_plugins
    initialize_plugins()

    from calibre.db.legacy import LibraryDatabase
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre_plugins.epub_layout_fix import jobs
    from calibre_plugins.epub_layout_fix.config import current_settings

    # calibre refuses library paths longer than 75 characters, and the usual temp directory is
    # already most of that, so anchor the throwaway library near the home directory instead.
    tmp = tempfile.mkdtemp(prefix='eplf', dir=os.path.expanduser('~'))
    try:
        lib = os.path.join(tmp, 'lib')
        os.makedirs(lib)
        ldb = LibraryDatabase(lib)
        db = ldb.new_api

        book = make_broken_epub(os.path.join(tmp, 'broken.epub'))
        mi = Metadata('Test Book', ['Test Author'])
        book_id = db.add_books([(mi, {'EPUB': book})], add_duplicates=True)[0][0]
        check('library', book_id is not None, 'book added with id %s' % book_id)
        check('library', 'EPUB' in {f.upper() for f in db.formats(book_id)},
              'EPUB format present')

        # --- exactly what the toolbar action does -------------------------------------
        work = os.path.join(tmp, 'work.epub')
        db.copy_format_to(book_id, 'EPUB', work)
        before = zipfile.ZipFile(work).read('p1.xhtml').decode()
        check('action', '<svg' not in before, 'the copy starts unfixed')

        res = jobs.process_book(work, current_settings(), polish_ops=None, target_version='3')
        check('action', not res['error'], 'pipeline ok: %s' % (res['error'] or 'yes'))
        check('action', res['changed'] and res['image_pages'] == 1,
              'one image page rewritten')

        db.save_original_format(book_id, 'EPUB')
        with open(work, 'rb') as f:
            db.add_format(book_id, 'EPUB', f, run_hooks=False)

        fmts = {f.upper() for f in db.formats(book_id)}
        check('backup', 'ORIGINAL_EPUB' in fmts,
              'ORIGINAL_EPUB created (formats: %s)' % ', '.join(sorted(fmts)))

        after = os.path.join(tmp, 'after.epub')
        db.copy_format_to(book_id, 'EPUB', after)
        t = zipfile.ZipFile(after).read('p1.xhtml').decode()
        check('result', '<svg' in t, 'the library copy is now fixed')
        for anchor in ('wrap', 'page_42', 'theimg'):
            check('result', 'id="%s"' % anchor in t, 'anchor %r preserved' % anchor)

        z = zipfile.ZipFile(after)
        i0 = z.infolist()[0]
        check('result', i0.filename == 'mimetype' and i0.compress_type == 0
              and z.testzip() is None, 'archive sound in the library')

        # --- the combined conversion window --------------------------------------------
        check_convert_dialog(ldb, book_id)

        # --- calibre's own Restore original --------------------------------------------
        db.restore_original_format(book_id, 'ORIGINAL_EPUB')
        restored = os.path.join(tmp, 'restored.epub')
        db.copy_format_to(book_id, 'EPUB', restored)
        rt = zipfile.ZipFile(restored).read('p1.xhtml').decode()
        check('restore', '<svg' not in rt, "calibre's Restore original reverts the fix")
        check('restore', 'ORIGINAL_EPUB' not in {f.upper() for f in db.formats(book_id)},
              'the ORIGINAL_EPUB format is consumed by the restore')
    finally:
        try:
            db.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%d failure(s)' % len(FAILURES))
    for f in FAILURES:
        print('   !!', f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
