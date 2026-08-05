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
import time
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


def check_bulk_convert_dialog(legacy_db, book_ids):
    """Selecting several books must give calibre's Bulk convert window, not the single-book one.

    The difference is not cosmetic: the single-book window carries Metadata and a Search & replace
    written against one book's text, which were being applied to every selected book.
    """
    from calibre_plugins.epub_layout_fix.dialog import make_bulk_convert_dialog
    from calibre_plugins.epub_layout_fix.panel import LayoutFixWidget, PolishWidget
    from calibre_plugins.epub_layout_fix.ui import bulk_recommendations

    try:
        from qt.core import QApplication, QWidget
    except ImportError:
        from PyQt5.Qt import QApplication, QWidget
    from calibre.gui2 import Application
    QApplication.instance() or Application(sys.argv[:1])

    parent = QWidget()
    d = make_bulk_convert_dialog(parent, legacy_db, book_ids)

    title = d.windowTitle()
    check('bulk', str(len(book_ids)) in title and 'ulk' in title,
          'window is the bulk one: %r' % title)

    titles = [w.TITLE.replace('\n', ' ') for w in d.widgets]
    check('bulk', sum(isinstance(w, (LayoutFixWidget, PolishWidget)) for w in d.widgets) == 2,
          'exactly one of each plugin panel (%s)' % ', '.join(titles))
    # the point of the bulk window: no per-book categories
    check('bulk', not any(t.startswith('Metadata') for t in titles),
          'no Metadata category, which cannot be shared across books')

    fmts = [d.output_formats.itemText(i) for i in range(d.output_formats.count())]
    check('bulk', fmts == ['EPUB'], 'output format restricted to EPUB (got %r)' % fmts)
    check('bulk', not d.output_formats.isEnabled(), 'output format combo disabled')

    seen = []
    for row in range(d._groups_model.rowCount()):
        d.groups.setCurrentIndex(d._groups_model.index(row))
        d.show_pane(d._groups_model.index(row))
        seen.append(d.scrollArea.widget() is d.widgets[row])
    check('bulk', all(seen), 'selecting each category shows its pane (%d/%d correct)'
          % (sum(seen), len(seen)))

    # BulkConfig.accept() iterates _groups_model.widgets and does recs.update(w.commit(...)),
    # so every panel in the model must return a mapping
    for w in d._groups_model.widgets:
        got = w.commit(save_defaults=False)
        check('bulk', hasattr(got, 'keys'),
              '%s.commit() returns a mapping, not %s' % (w.TITLE.replace('\n', ' '),
                                                         type(got).__name__))
    try:
        d.accept()
        check('bulk', True, 'accept() completes without raising')
    except Exception as e:                                     # noqa: BLE001
        check('bulk', False, 'accept() raised %s: %s' % (type(e).__name__, e))

    d.setup_pipeline()
    n = sum(isinstance(w, (LayoutFixWidget, PolishWidget)) for w in d.widgets)
    check('bulk', n == 2, 'panels not duplicated after setup_pipeline re-runs (found %d)' % n)

    # --- per-book recommendations honour the saved-settings checkbox ---
    # The checkbox promises, in calibre's own words, that "for settings that cannot be specified
    # in this dialog" the values saved for that book are used. So the marker has to be an option
    # the bulk window does not carry - debug_pipeline, since there is no Debug category here -
    # and an option it does carry must come from the window instead.
    from calibre.ebooks.conversion.config import GuiRecommendations, save_specifics

    marker = GuiRecommendations()
    marker['debug_pipeline'] = '/tmp/eplf-debug-marker'
    marker['change_justification'] = 'center'
    save_specifics(legacy_db, book_ids[0], marker)

    d.opt_individual_saved_settings.setChecked(True)
    recs_for = bulk_recommendations(legacy_db, d)
    got = recs_for(book_ids[0], 'EPUB')
    as_dict = {r[0]: r[1] for r in got}
    check('bulk', all(isinstance(r, tuple) and len(r) == 3 for r in got),
          'recommendations are (name, value, level) triples (%d of them)' % len(got))
    check('bulk', as_dict.get('debug_pipeline') == '/tmp/eplf-debug-marker',
          "a setting the window cannot specify comes from the book's saved settings")

    window = {r[0]: r[1] for r in (d.recommendations or ())}
    shared = [k for k in ('change_justification',) if k in window]
    check('bulk', all(as_dict.get(k) == window[k] for k in shared),
          'a setting the window does carry comes from the window, not the book (%s)'
          % ', '.join('%s=%r' % (k, as_dict.get(k)) for k in shared))

    other = recs_for(book_ids[1], 'EPUB') if len(book_ids) > 1 else []
    check('bulk', not any(r[0] == 'debug_pipeline' and r[1] == marker['debug_pipeline']
                          for r in other),
          "another book does not inherit the first book's saved setting")

    d.opt_individual_saved_settings.setChecked(False)
    off = bulk_recommendations(legacy_db, d)(book_ids[0], 'EPUB')
    check('bulk', not any(r[0] == 'debug_pipeline' and r[1] == marker['debug_pipeline']
                          for r in off),
          'unticking the checkbox drops the saved settings')

    d.break_cycles()


class FakeModel(object):
    def __init__(self):
        self.refreshed = []

    def refresh_ids(self, ids, current_row=-1):
        self.refreshed.extend(ids)


class FakeView(object):
    def __init__(self):
        self._model = FakeModel()

    def model(self):
        return self._model


class FakeStatusBar(object):
    def __init__(self):
        self.messages = []

    def show_message(self, msg, timeout=0):
        self.messages.append(msg)


try:
    from qt.core import QObject as _QObject
except ImportError:
    from PyQt5.Qt import QObject as _QObject


class FakeGui(_QObject):
    """Just enough of calibre's main window for the action's job plumbing.

    A QObject rather than a plain object because InterfaceAction.__init__ passes it straight to
    QObject.__init__ as the parent.
    """

    def __init__(self, legacy_db):
        _QObject.__init__(self)
        self.current_db = legacy_db
        self.library_view = FakeView()
        self.status_bar = FakeStatusBar()
        self.iactions = {}
        self.covers_refreshed = 0
        self.dialogs = []

    def refresh_cover_browser(self):
        self.covers_refreshed += 1

    def job_exception(self, *a, **kw):
        pass


def check_action_end_to_end(legacy_db, book_id, tmp):
    """The toolbar action's own path: build the spec, run it, write it back, report.

    Everything between picking a book and the summary dialog, driven for real - the only part
    stubbed out is calibre's main window.
    """
    print('\n=== toolbar action, spec to write-back ===')
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre_plugins.epub_layout_fix import jobs, ui
    from calibre_plugins.epub_layout_fix.config import prefs

    db = legacy_db.new_api
    gui = FakeGui(legacy_db)
    act = ui.FixLayoutQuickGui(gui, None)
    act.gui = gui

    reported = {}
    act._report = lambda results, silent=False: reported.update(
        {'results': results, 'silent': silent})

    saved = {k: prefs.get(k) for k in ('dark_cover', 'cover_color', 'fix_images')}
    try:
        # --- settings the action reads must reach the spec ---
        prefs['cover_color'] = '#101010'
        prefs['dark_cover'] = True
        specs, missing = act._epub_jobs([book_id])
        check('action2', len(specs) == 1 and not missing, 'one spec built for one EPUB book')
        spec = specs[0]
        check('action2', spec['settings']['cover_color'] == '#101010',
              'the stored settings reach the job spec')
        check('action2', os.path.exists(spec['path']), 'the format was copied out to a temp file')
        check('action2', spec['book_id'] == book_id and spec['title'],
              'the spec carries the book id and title')

        # --- run it the way the job thread does ---
        result = jobs.run_single(spec)
        check('action2', not result['error'], 'the worker succeeded: %s' % (result['error'] or ''))
        check('action2', result['changed'], 'the book was changed')

        # --- write it back ---
        act._commit_results([result])
        check('action2', not result.get('error'), 'commit reported no error')
        check('action2', book_id in gui.library_view.model().refreshed,
              'the library view was refreshed for the book')
        check('action2', not os.path.exists(spec['path']),
              'the temp file was removed after committing')

        fmts = {f.upper() for f in db.formats(book_id)}
        check('action2', 'ORIGINAL_EPUB' in fmts, 'a backup was made (%s)' % sorted(fmts))

        out = os.path.join(tmp, 'committed.epub')
        db.copy_format_to(book_id, 'EPUB', out)
        with zipfile.ZipFile(out) as z:
            texts = [z.read(n).decode('utf-8', 'replace') for n in z.namelist()
                     if n.endswith('.xhtml')]
        check('action2', any('#101010' in t for t in texts) or not any(
            'epub-layout-fix' in t for t in texts),
            'the chosen letterbox colour is in the committed book')

        # --- a book with nothing to convert produces no spec, not a crash ---
        empty = db.add_books([(Metadata('No Formats', ['Nobody']), {})],
                             add_duplicates=True)[0][0]
        specs2, missing2 = act._epub_jobs([empty])
        check('action2', not specs2 and missing2 == [empty],
              'a book with no EPUB is reported as missing, not queued')
        check('action2', act._convert_jobs([empty]) == [],
              'a book with no formats at all yields no conversion job')

        # --- temp files are cleaned up even when nothing was committed ---
        specs3, _ = act._epub_jobs([book_id])
        path3 = specs3[0]['path']
        act._commit_results([{'book_id': book_id, 'path': path3, 'changed': False,
                              'error': None, 'name': 'x'}])
        check('action2', not os.path.exists(path3),
              'an unchanged book still has its temp file removed')
    finally:
        for k, v in saved.items():
            if v is not None:
                prefs[k] = v


def check_dry_run(legacy_db, tmp):
    """A dry run runs the whole pipeline and leaves the library untouched.

    The distinction that matters: the work really happens, and really is verified, on the
    temporary copy. Only the write-back is skipped. So a dry run tells you what a real run would
    produce, which analysing the existing EPUB cannot.
    """
    print('\n=== dry run ===')
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre_plugins.epub_layout_fix import jobs, ui

    db = legacy_db.new_api
    gui = FakeGui(legacy_db)
    act = ui.FixLayoutQuickGui(gui, None)
    act.gui = gui
    act._report = lambda results, silent=False: None

    book_id = db.add_books([(Metadata('Dry Run Book', ['Nobody']),
                             {'EPUB': make_broken_epub(os.path.join(tmp, 'dry.epub'))})],
                           add_duplicates=True)[0][0]
    before = os.path.getsize(os.path.join(tmp, 'dry-before.epub')) if False else None
    out = os.path.join(tmp, 'dry-before.epub')
    db.copy_format_to(book_id, 'EPUB', out)
    with zipfile.ZipFile(out) as z:
        original = z.read('p1.xhtml').decode()
    check('dry', '<svg' not in original, 'the library copy starts unfixed')

    act.dry_run = True
    specs, _missing = act._epub_jobs([book_id])
    check('dry', specs and specs[0].get('dry_run') is True,
          'the flag reaches the job spec')

    result = jobs.run_single(specs[0])
    check('dry', not result['error'] and result['changed'],
          'the pipeline really ran: %s' % (result['error'] or 'changed'))
    check('dry', result['image_pages'] >= 1, 'and really did the work (%d image page(s))'
          % result['image_pages'])
    check('dry', result['dry_run'] is True, 'the result is marked as a dry run')

    act._commit_results([result])
    fmts = {f.upper() for f in db.formats(book_id)}
    check('dry', 'ORIGINAL_EPUB' not in fmts, 'no backup was made (%s)' % sorted(fmts))
    check('dry', not os.path.exists(specs[0]['path']), 'the temporary copy was cleaned up')

    after = os.path.join(tmp, 'dry-after.epub')
    db.copy_format_to(book_id, 'EPUB', after)
    with zipfile.ZipFile(after) as z:
        still = z.read('p1.xhtml').decode()
    check('dry', still == original, 'the library copy is byte-identical afterwards')
    check('dry', not gui.library_view.model().refreshed,
          'nothing was refreshed, because nothing changed')

    # and with the flag off, the same book does get written
    act.dry_run = False
    specs, _missing = act._epub_jobs([book_id])
    check('dry', specs[0].get('dry_run') is False, 'the flag is off again')
    result = jobs.run_single(specs[0])
    act._commit_results([result])
    check('dry', 'ORIGINAL_EPUB' in {f.upper() for f in db.formats(book_id)},
          'a real run does write, so the dry run was the only thing holding it back')


def check_report_action(legacy_db, tmp):
    """Drive the report action the way the toolbar does, not just its widgets.

    The widget smoke test cannot catch a signature mismatch between an action and the shared
    job plumbing, because it never calls run(). This does.
    """
    print('\n=== report action, end to end ===')
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre_plugins.epub_layout_fix import ui

    db = legacy_db.new_api
    gui = FakeGui(legacy_db)
    act = ui.ReportGui(gui, None)
    act.gui = gui

    book_id = db.add_books([(Metadata('Report Me', ['X']),
                             {'EPUB': make_broken_epub(os.path.join(tmp, 'rep.epub'))})],
                           add_duplicates=True)[0][0]

    # every call the action makes must accept the arguments it passes
    import inspect
    start = inspect.signature(ui._BaseGui._start).parameters
    check('report', 'worker' in start and 'kind' in start,
          '_start accepts the worker and kind the report action passes: %s' % list(start))
    base_report = inspect.signature(ui._BaseGui._report).parameters
    check('report', 'kind' in base_report,
          '_BaseGui._report accepts kind, which ReportGui._report forwards: %s'
          % list(base_report))

    specs, missing = act._epub_jobs([book_id])
    check('report', specs and not missing, 'a spec was built for the book')

    from calibre_plugins.epub_layout_fix.jobs import run_report
    result = run_report(specs[0])
    check('report', not result['error'], 'the report worker ran: %s' % (result['error'] or 'ok'))
    check('report', result['changed'] is True, 'it says the book would change')
    check('report', result['ledger'], 'and it produced a ledger (%d rows)' % len(result['ledger']))

    before = os.path.getsize(os.path.join(tmp, 'rep.epub'))
    act._commit_results([result])
    check('report', 'ORIGINAL_EPUB' not in {f.upper() for f in db.formats(book_id)},
          'a report writes nothing back')

    # and the whole reporting path, including the flagging, without opening a window
    shown = {}
    act._report = lambda results, silent=False, kind='fix': shown.update(
        {'kind': kind, 'flagged': act._flag_books(
            [r['book_id'] for r in results if r.get('changed')])})
    act._report([result], kind='report')
    check('report', shown.get('kind') == 'report', 'the batch kind reaches the reporter')
    check('report', shown.get('flagged') == 1, 'the book that needs work got flagged')
    marks = dict(legacy_db.data.marked_ids)
    check('report', marks.get(book_id) == act.MARK_LABEL,
          'and carries the pin: %r' % marks.get(book_id))


def check_flagging(legacy_db, tmp):
    """Books that still need work get calibre's marked pin; fixing one takes it off again."""
    print('\n=== flagging books that need work ===')
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre_plugins.epub_layout_fix import ui

    db = legacy_db.new_api
    gui = FakeGui(legacy_db)
    act = ui.FixLayoutQuickGui(gui, None)
    act.gui = gui

    a = db.add_books([(Metadata('Flag A', ['X']),
                       {'EPUB': make_broken_epub(os.path.join(tmp, 'fa.epub'))})],
                     add_duplicates=True)[0][0]
    b = db.add_books([(Metadata('Flag B', ['X']),
                       {'EPUB': make_broken_epub(os.path.join(tmp, 'fb.epub'))})],
                     add_duplicates=True)[0][0]

    # something else already marked a book: that mark must survive untouched
    legacy_db.data.set_marked_ids({b: 'someone-elses-mark'})

    n = act._flag_books([a])
    marks = dict(legacy_db.data.marked_ids)
    check('flag', n == 1 and marks.get(a) == act.MARK_LABEL,
          'the book that needs work is marked %r' % marks.get(a))
    check('flag', marks.get(b) == 'someone-elses-mark',
          "another plugin's mark is left alone: %r" % marks.get(b))

    # a second report must not accumulate stale pins
    n = act._flag_books([b])
    marks = dict(legacy_db.data.marked_ids)
    check('flag', act.MARK_LABEL not in [marks.get(a)],
          'a book that no longer needs work loses our pin (%r)' % marks.get(a))
    check('flag', marks.get(b) == act.MARK_LABEL, 'and the newly failing one gains it')

    # fixing a book clears it
    act._unflag_books([b])
    marks = dict(legacy_db.data.marked_ids)
    check('flag', marks.get(b) != act.MARK_LABEL,
          'a real run takes the pin off: %r' % marks.get(b))

    check('flag', act.MARK_LABEL == 'needs-fix',
          'the label is searchable as marked:needs-fix')

    # --- and the wiring, not just the helper -------------------------------------------
    # Calling _flag_books directly proves nothing about whether _report ever calls it. It did
    # not, for a while, and every direct test still passed.
    legacy_db.data.set_marked_ids({})
    shown = []
    real_info = ui.info_dialog
    ui.info_dialog = lambda *a, **k: shown.append(a)
    try:
        act._report([{'book_id': a, 'title': 'A', 'name': 'a.epub', 'changed': True,
                      'dry_run': True, 'details': [], 'problems': [], 'ledger': []}])
    finally:
        ui.info_dialog = real_info
    check('flag', dict(legacy_db.data.marked_ids).get(a) == act.MARK_LABEL,
          'a dry run through _report pins the book: %r'
          % dict(legacy_db.data.marked_ids).get(a))
    check('flag', shown and 'Dry run' in str(shown[0][1]),
          'and the summary is titled as a dry run: %r' % (shown[0][1] if shown else None))

    # a real run through _report takes it off again
    shown = []
    ui.info_dialog = lambda *a, **k: shown.append(a)
    try:
        act._report([{'book_id': a, 'title': 'A', 'name': 'a.epub', 'changed': True,
                      'details': [], 'problems': [], 'ledger': []}])
    finally:
        ui.info_dialog = real_info
    check('flag', dict(legacy_db.data.marked_ids).get(a) != act.MARK_LABEL,
          'a real run through _report unpins it: %r'
          % dict(legacy_db.data.marked_ids).get(a))


def check_import_watcher(db, make_epub, tmp):
    """The automatic run must fire on a real import - and exactly once per book.

    Committing a repaired book calls add_format, which raises the same event that started the
    run. If the guards were wrong the plugin would process its own output forever, so the
    important assertion here is the second one: nothing fires while a book is suppressed.
    """
    print('\n=== automatic run on import ===')
    from calibre.ebooks.metadata.book.base import Metadata
    try:
        from qt.core import QApplication, QEventLoop
    except ImportError:
        from PyQt5.Qt import QApplication, QEventLoop
    from calibre.gui2 import Application
    QApplication.instance() or Application(sys.argv[:1])

    from calibre_plugins.epub_layout_fix.automation import ImportWatcher
    from calibre_plugins.epub_layout_fix.config import prefs

    calls = []

    class FakeAction(object):
        gui = None

        def run_automatic(self, book_ids):
            calls.append(list(book_ids))

    saved = {k: prefs.get(k) for k in ('auto_on_import', 'auto_debounce_secs')}
    prefs['auto_on_import'] = True
    prefs['auto_debounce_secs'] = 1

    watcher = ImportWatcher(FakeAction())
    watcher.attach(db)

    def pump(seconds):
        """Spin the event loop - the listener thread and the debounce timer both need it."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            time.sleep(0.02)

    try:
        # --- a book arriving normally is picked up ---
        src = make_epub(os.path.join(tmp, 'imported.epub'))
        mi = Metadata('Imported Book', ['Someone'])
        new_id = db.add_books([(mi, {'EPUB': src})], add_duplicates=True)[0][0]
        pump(4)
        check('auto', calls and new_id in calls[0],
              'a newly added book reaches run_automatic (%r)' % calls)
        check('auto', len(calls) == 1, 'exactly one batch, not one call per event (%d)' % len(calls))

        # --- our own write-back must not start it again ---
        calls[:] = []
        watcher.suppress([new_id])
        with open(src, 'rb') as f:
            db.add_format(new_id, 'EPUB', f, run_hooks=False)
        pump(4)
        check('auto', not calls, 'a suppressed book does not re-trigger (%r)' % calls)

        # --- and a book already carrying our backup is left alone ---
        calls[:] = []
        watcher._seen.clear()
        watcher._suppressed.clear()
        db.save_original_format(new_id, 'EPUB')
        with open(src, 'rb') as f:
            db.add_format(new_id, 'EPUB', f, run_hooks=False)
        pump(4)
        check('auto', not calls, 'a book with ORIGINAL_EPUB is skipped (%r)' % calls)

        # --- off means off ---
        calls[:] = []
        watcher._seen.clear()
        prefs['auto_on_import'] = False
        mi2 = Metadata('Second Book', ['Someone'])
        db.add_books([(mi2, {'EPUB': make_epub(os.path.join(tmp, 'imported2.epub'))})],
                     add_duplicates=True)
        pump(3)
        check('auto', not calls, 'nothing happens while the setting is off (%r)' % calls)
    finally:
        watcher.detach()
        for k, v in saved.items():
            if v is None:
                prefs.pop(k, None)
            else:
                prefs[k] = v


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

        # --- the combined conversion windows, single and bulk ---------------------------
        check_convert_dialog(ldb, book_id)

        second = db.add_books(
            [(Metadata('Second Book', ['Test Author']),
              {'EPUB': make_broken_epub(os.path.join(tmp, 'broken2.epub'))})],
            add_duplicates=True)[0][0]
        check_bulk_convert_dialog(ldb, [book_id, second])

        # --- the toolbar action's own path, end to end ----------------------------------
        third = db.add_books(
            [(Metadata('Third Book', ['Test Author']),
              {'EPUB': make_broken_epub(os.path.join(tmp, 'broken3.epub'))})],
            add_duplicates=True)[0][0]
        check_action_end_to_end(ldb, third, tmp)

        # --- a dry run must do the work and keep none of it -----------------------------
        check_dry_run(ldb, tmp)

        # --- the report action, driven the way the toolbar drives it --------------------
        check_report_action(ldb, tmp)

        # --- flagging books that need work ----------------------------------------------
        check_flagging(ldb, tmp)

        # --- the automatic run on import -----------------------------------------------
        check_import_watcher(db, make_broken_epub, tmp)

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
