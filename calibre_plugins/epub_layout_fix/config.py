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
prefs.defaults['write_audit_csv'] = False
prefs.defaults['audit_csv_path'] = ''

#: (stored value, label). EPUB 3 is the default because the nav document, the modern manifest
#: and the properties="svg" attribute only exist there.
EPUB_VERSIONS = (
    ('3', 'EPUB 3 (recommended)'),
    ('2', 'EPUB 2 (leave as-is)'),
)


def current_settings():
    """The settings dict the engine expects."""
    return {k: prefs.get(k, v) for k, v in DEFAULT_SETTINGS.items()}


def target_epub_version():
    v = str(prefs.get('target_epub_version', '3'))
    return v if v in ('2', '3') else '3'


def polish_settings():
    """-> ``(enabled, {operation: bool})`` for the polish stage."""
    from calibre_plugins.epub_layout_fix.panel import calibre_polish_defaults
    ops = dict(prefs.get('polish_ops') or {})
    if not ops:
        ops = calibre_polish_defaults()
        ops.setdefault('upgrade_book', True)
    return bool(prefs.get('polish_enabled', True)), ops


class ConfigWidget(object):
    """Preferences -> Plugins -> Customize.

    Hosts the very same panels the conversion window shows, so there is one implementation of
    each setting rather than two that can drift apart.
    """

    def __new__(cls):
        try:
            from qt.core import QTabWidget, QVBoxLayout, QWidget
        except ImportError:
            from PyQt5.Qt import QTabWidget, QVBoxLayout, QWidget
        from calibre_plugins.epub_layout_fix.panel import LayoutFixWidget, PolishWidget

        w = QWidget()
        layout = QVBoxLayout(w)
        tabs = QTabWidget(w)
        layout.addWidget(tabs)

        w._panels = [LayoutFixWidget(w), PolishWidget(w)]
        for p in w._panels:
            tabs.addTab(p, p.TITLE)

        def save_settings(_self=w):
            for panel in _self._panels:
                panel.save()

        w.save_settings = save_settings
        return w
