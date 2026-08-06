#!/usr/bin/env python3
"""Headless smoke test for the Qt pieces.

Run inside calibre's interpreter, with no display:

    set QT_QPA_PLATFORM=offscreen
    calibre-debug tests/smoke_gui.py

Instantiates the panels and the compact dialog for real, which catches layout and API errors
that importing alone would not.
"""

import sys
import traceback

FAILURES = []


def check(name, fn):
    try:
        fn()
        print('  PASS  %s' % name)
    except Exception:
        print('  FAIL  %s' % name)
        traceback.print_exc()
        FAILURES.append(name)


def main():
    from calibre.customize.ui import initialize_plugins
    initialize_plugins()

    try:
        from qt.core import QApplication
    except ImportError:
        from PyQt5.Qt import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])

    state = {}

    def import_modules():
        from calibre_plugins.epub_layout_fix import (action_base, config, dialog, fixer,
                                                     jobs, panel, ui)
        state['mods'] = (action_base, config, dialog, fixer, jobs, panel, ui)

    check('modules import', import_modules)
    if FAILURES:
        return 1

    action_base, config, dialog, fixer, jobs, panel, ui = state['mods']

    def actions_registered():
        from calibre.customize.ui import initialized_plugins
        names = {p.name for p in initialized_plugins()}
        for want in ('EPUB Layout Fix', 'EPUB Layout Fix - quick run',
                     'EPUB Layout Fix - convert and fix', 'EPUB Layout Fix - report'):
            assert want in names, 'missing action: %s' % want
    check('all four actions registered', actions_registered)

    def build_report_dialog():
        from calibre_plugins.epub_layout_fix.report_dialog import ReportDialog
        results = [
            {'book_id': 1, 'title': 'Needs work', 'name': 'a.epub', 'changed': True,
             'image_pages': 2, 'svg_repaired': 0, 'cover_fixed': True, 'dead_links': 1,
             'skipped': 3,
             'ledger': [{'page': 'p1.xhtml', 'action': 'rewrite', 'category': 'full-page-image',
                         'reason': 'effective width 100%', 'width': 1200, 'height': 1800},
                        {'page': 'p2.xhtml', 'action': 'skip', 'category': 'too-narrow',
                         'reason': 'effective width 40%', 'width': 400, 'height': 600}]},
            {'book_id': 2, 'title': 'Already fine', 'name': 'b.epub', 'changed': False,
             'ledger': []},
            {'book_id': 3, 'title': 'Broken', 'name': 'c.epub', 'error': 'unreadable',
             'ledger': []},
        ]
        d = ReportDialog(None, results)
        assert d.tree.topLevelItemCount() == 3, 'one row per book'
        assert d.tree.topLevelItem(0).childCount() == 2, 'ledger rows hang off the book'
        rows = d.rows()
        assert len(rows) == 4, 'flattened export covers every ledger row plus the empties: %d' % len(rows)
        assert rows[0][1] == 'p1.xhtml' and rows[0][3] == 'full-page-image', rows[0]
    check('ReportDialog builds and flattens', build_report_dialog)

    def csv_is_not_a_formula():
        """Every exported field is book text, so it is whatever the EPUB said.

        Excel and LibreOffice evaluate a cell starting with =, +, - or @, which makes a book
        titled "=cmd|'/c calc'!A1" run when the report is opened.
        """
        from calibre_plugins.epub_layout_fix.report_dialog import ReportDialog
        for hostile in ("=cmd|'/c calc'!A1", '+1234', '-5', '@SUM(1+1)', '\tx', '\rx'):
            got = ReportDialog._csv_safe(hostile)
            assert got.startswith("'"), 'not neutralised: %r -> %r' % (hostile, got)
            assert got[1:] == hostile, 'text was altered: %r -> %r' % (hostile, got)
        for ordinary in ('Normal Title', 'a=b', '', '1200'):
            assert ReportDialog._csv_safe(ordinary) == ordinary, ordinary
        assert ReportDialog._csv_safe(None) == '', 'None becomes empty, not "None"'
        assert ReportDialog._csv_safe(1200) == '1200', 'numbers survive as text'
    check('CSV export cannot smuggle a spreadsheet formula', csv_is_not_a_formula)

    def polish_ops():
        ops = panel.polish_operations()
        assert ops, 'no polish operations discovered'
        keys = {k for k, _l, _h in ops}
        assert 'upgrade_book' in keys, 'upgrade_book missing'
        assert 'cover' not in keys and 'opf' not in keys, 'file-argument options should be hidden'
        print('        %d operations: %s' % (len(ops), ', '.join(sorted(keys))))
    check('polish operations come from calibre', polish_ops)

    def build_layout_panel():
        w = panel.LayoutFixWidget(None)
        assert w.TITLE
        w.commit()
        state['layout_panel'] = w
    check('LayoutFixWidget builds and commits', build_layout_panel)

    def build_polish_panel():
        w = panel.PolishWidget(None)
        assert w.boxes, 'no operation checkboxes'
        w.commit()
    check('PolishWidget builds and commits', build_polish_panel)

    def build_automation_panel():
        w = panel.AutomationWidget(None)
        assert w.TITLE
        w.commit()
        # the master switch has to survive a round-trip, and default to off
        assert isinstance(config.auto_settings()['enabled'], bool)
    check('AutomationWidget builds and commits', build_automation_panel)

    def build_dialog():
        d = dialog.FixOnlyDialog(None)
        assert d.categories.count() == 2, 'expected two categories'
        assert d.stack.count() == 2
    check('FixOnlyDialog builds', build_dialog)

    def build_config_widget():
        w = config.ConfigWidget()
        titles = [p.TITLE for p in w.panels]
        assert 'Automatic' in titles, 'Automatic panel missing from the config widget: %s' % titles
        w.save_settings()
    check('ConfigWidget builds all three panels', build_config_widget)

    def settings_roundtrip():
        s = config.current_settings()
        for key in fixer.DEFAULT_SETTINGS:
            assert key in s, 'missing setting %s' % key
        enabled, ops = config.polish_settings()
        assert isinstance(ops, dict)
        assert config.target_epub_version() in ('2', '3')

        # nothing that reaches out to the network or scans the machine may run unattended
        _en, auto_ops = config.polish_settings(automatic=True)
        for key in config.AUTO_POLISH_EXCLUDED:
            assert not auto_ops.get(key), '%s must be off for automatic runs' % key

        a = config.auto_settings()
        assert set(a) == {'enabled', 'convert_formats', 'debounce', 'max_books'}
        assert 1 <= a['debounce'] <= 60
    check('settings round-trip', settings_roundtrip)

    def engine_is_gui_free():
        import inspect
        src = inspect.getsource(fixer)
        for bad in ('from qt', 'import qt', 'PyQt', 'from calibre'):
            assert bad not in src, 'engine imports %r - it must stay headless' % bad
    check('engine has no calibre/Qt imports', engine_is_gui_free)

    print('\n%d failure(s)' % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
