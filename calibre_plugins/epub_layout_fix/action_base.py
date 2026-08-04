#!/usr/bin/env python3
"""The InterfaceActionBase declarations.

One per toolbar button. calibre lists each separately under
Preferences -> Toolbars & menus, so only the wanted ones need placing.
"""

from __future__ import annotations

from calibre.customize import InterfaceActionBase

__license__ = 'GPL v3'

PLUGIN_VERSION = (0, 1, 0)
PLUGIN_AUTHOR = 'devnullv0id'
MIN_CALIBRE = (5, 0, 0)


class _Base(InterfaceActionBase):
    supported_platforms = ['windows', 'osx', 'linux']
    author = PLUGIN_AUTHOR
    version = PLUGIN_VERSION
    minimum_calibre_version = MIN_CALIBRE

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.epub_layout_fix.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()


class ConvertAndFixAction(_Base):
    name = 'EPUB Layout Fix - convert and fix'
    description = ('Convert the selected books to EPUB using the normal conversion window, with '
                   'an added Layout fixes panel, then repair full-page images and covers')
    actual_plugin = 'calibre_plugins.epub_layout_fix.ui:ConvertAndFixGui'


class FixLayoutQuickAction(_Base):
    name = 'EPUB Layout Fix - quick run'
    description = ('Repair the selected books immediately using the stored settings, without '
                   'showing a dialog')
    actual_plugin = 'calibre_plugins.epub_layout_fix.ui:FixLayoutQuickGui'


class FixLayoutAction(_Base):
    """The primary plugin.

    calibre loads exactly one Plugin subclass per zip, so the two companion actions are
    registered here at runtime by appending them to calibre's initialized-plugin list. This is
    the same approach the KFX Input plugin uses to contribute several plugins from one archive,
    and it is what makes all three appear separately under Preferences -> Toolbars & menus.
    """

    name = 'EPUB Layout Fix'
    description = ('Repair full-page images and covers in EPUB books so they fit the screen. '
                   'Adds three toolbar actions.')
    actual_plugin = 'calibre_plugins.epub_layout_fix.ui:FixLayoutGui'

    #: registered alongside this one
    COMPANIONS = (ConvertAndFixAction, FixLayoutQuickAction)

    def initialize(self):
        try:
            from calibre.customize.ui import _initialized_plugins
        except ImportError:      # pragma: no cover - calibre internals moved
            return

        for pi_type in self.COMPANIONS:
            if any(isinstance(p, pi_type) for p in _initialized_plugins):
                continue
            pi_type.version = self.version
            plugin = pi_type(self.plugin_path)
            _initialized_plugins.append(plugin)
            plugin.initialize()
