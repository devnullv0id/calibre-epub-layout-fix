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
        for want in ('Fix EPUB layout', 'Fix EPUB layout (last settings)',
                     'Convert to EPUB and fix layout'):
            assert want in names, 'missing action: %s' % want
    check('all three actions registered', actions_registered)

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

    def build_dialog():
        d = dialog.FixOnlyDialog(None)
        assert d.categories.count() == 2, 'expected two categories'
        assert d.stack.count() == 2
    check('FixOnlyDialog builds', build_dialog)

    def settings_roundtrip():
        s = config.current_settings()
        for key in fixer.DEFAULT_SETTINGS:
            assert key in s, 'missing setting %s' % key
        enabled, ops = config.polish_settings()
        assert isinstance(ops, dict)
        assert config.target_epub_version() in ('2', '3')
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
