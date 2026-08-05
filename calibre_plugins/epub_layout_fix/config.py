#!/usr/bin/env python3
"""Persisted settings, plus the widget shown in Preferences -> Plugins -> Customize."""

from __future__ import annotations

from calibre.utils.config import JSONConfig

from calibre_plugins.epub_layout_fix.fixer import DEFAULT_SETTINGS

__license__ = 'GPL v3'

#: Stored under calibre's config dir as plugins/epub_layout_fix.json
prefs = JSONConfig('plugins/epub_layout_fix')
prefs.defaults.update(DEFAULT_SETTINGS)
prefs.defaults['target_epub_version'] = '3'
prefs.defaults['polish_enabled'] = True
prefs.defaults['polish_ops'] = {}          # empty -> seeded from calibre's own polish settings

#: Pretty-print every file. Off by default: it changes nothing about the rendered book and
#: rewrites every document, so a book needing no repair still comes out "changed".
prefs.defaults['beautify'] = False

#: Process the book for real, then throw the result away instead of saving it. Not an engine
#: setting - the engine always does the same work; this only decides whether it is kept.
prefs.defaults['dry_run'] = False

#: Automatic runs on import. Off by default: this plugin rewrites books, and nothing should start
#: doing that to a library without being asked.
prefs.defaults['auto_on_import'] = False
prefs.defaults['auto_convert_formats'] = True   # non-EPUB -> convert to EPUB, keep the source
prefs.defaults['auto_debounce_secs'] = 3        # collect one import into a single batch
prefs.defaults['auto_max_books'] = 100          # confirm once above this many

#: (stored value, label). EPUB 3 is the default because the nav document, the modern manifest
#: and the properties="svg" attribute only exist there.
EPUB_VERSIONS = (
    ('3', 'EPUB 3 (recommended)'),
    ('2', 'EPUB 2 (leave as-is)'),
)

#: Polish operations never run automatically, whatever calibre's saved settings say. "Embed
#: referenced fonts" scans this computer and copies matching fonts into the book - it duplicated
#: an already-embedded font in testing - and "Download external resources" fetches remote URLs.
#: Neither belongs in something that fires unattended on every import.
AUTO_POLISH_EXCLUDED = frozenset({'embed', 'download_external_resources'})


def current_settings():
    """The settings dict the engine expects."""
    return {k: prefs.get(k, v) for k, v in DEFAULT_SETTINGS.items()}


def target_epub_version():
    v = str(prefs.get('target_epub_version', '3'))
    return v if v in ('2', '3') else '3'


def polish_settings(automatic=False):
    """-> ``(enabled, {operation: bool})`` for the polish stage."""
    from calibre_plugins.epub_layout_fix.panel import calibre_polish_defaults
    ops = dict(prefs.get('polish_ops') or {})
    if not ops:
        ops = calibre_polish_defaults()
        ops.setdefault('upgrade_book', True)
    if automatic:
        for key in AUTO_POLISH_EXCLUDED:
            ops[key] = False
    return bool(prefs.get('polish_enabled', True)), ops


def beautify_enabled():
    return bool(prefs.get('beautify', False))


def dry_run_enabled():
    return bool(prefs.get('dry_run', False))


def auto_settings():
    """-> the automatic-run settings, sanitised."""
    return {
        'enabled': bool(prefs.get('auto_on_import', False)),
        'convert_formats': bool(prefs.get('auto_convert_formats', True)),
        'debounce': max(1, min(60, int(prefs.get('auto_debounce_secs', 3) or 3))),
        'max_books': max(1, int(prefs.get('auto_max_books', 100) or 100)),
    }


try:
    from qt.core import QTabWidget, QVBoxLayout, QWidget
except ImportError:                                            # older calibre
    from PyQt5.Qt import QTabWidget, QVBoxLayout, QWidget


class ConfigWidget(QWidget):
    """Preferences -> Plugins -> Customize.

    Hosts the very same panels the conversion window shows, so there is one implementation of
    each setting rather than two that can drift apart. The Automatic panel is only here: it has
    nothing to say about a conversion the user started by hand.
    """

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        from calibre_plugins.epub_layout_fix.panel import (AutomationWidget, LayoutFixWidget,
                                                           PolishWidget)

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        self.panels = [LayoutFixWidget(self), PolishWidget(self), AutomationWidget(self)]
        for p in self.panels:
            tabs.addTab(p, p.TITLE)

    def save_settings(self):
        for panel in self.panels:
            panel.save()
