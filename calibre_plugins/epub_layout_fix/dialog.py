#!/usr/bin/env python3
"""The two windows.

:class:`ConvertAndFixDialog` is calibre's own conversion window with our panels appended, so it
carries the full Metadata / Look & feel / Page setup / ... set unchanged and adds Polish and
Layout fixes categories.

:class:`FixOnlyDialog` is the compact window for the no-conversion case: the same two panels
without the conversion machinery.
"""

from __future__ import annotations

try:
    from qt.core import (QDialog, QDialogButtonBox, QIcon, QListWidget, QListWidgetItem,
                         QHBoxLayout, QSize, QStackedWidget, QVBoxLayout)
except ImportError:                                            # older calibre
    from PyQt5.Qt import (QDialog, QDialogButtonBox, QIcon, QListWidget, QListWidgetItem,
                          QHBoxLayout, QSize, QStackedWidget, QVBoxLayout)

from calibre.gui2 import gprefs

from calibre_plugins.epub_layout_fix.panel import LayoutFixWidget, PolishWidget

__license__ = 'GPL v3'


def _icon(name):
    """calibre's themed icon lookup, with a graceful fallback across versions."""
    try:
        return QIcon.ic(name)
    except Exception:                                          # noqa: BLE001
        pass
    try:
        from calibre.gui2 import icon_from_name
        return icon_from_name(name)
    except Exception:                                          # noqa: BLE001
        return QIcon()


class FixOnlyDialog(QDialog):
    """Compact window: our panels only, for fixing an existing EPUB."""

    def __init__(self, parent, title=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(title or _('Fix EPUB layout'))
        self.setWindowIcon(_icon('format-fill-color.png'))

        outer = QVBoxLayout(self)
        row = QHBoxLayout()
        outer.addLayout(row)

        self.categories = QListWidget(self)
        self.categories.setIconSize(QSize(32, 32))
        self.categories.setMaximumWidth(200)
        row.addWidget(self.categories)

        self.stack = QStackedWidget(self)
        row.addWidget(self.stack, 1)

        self.panels = [LayoutFixWidget(self), PolishWidget(self)]
        for p in self.panels:
            self.stack.addWidget(p)
            item = QListWidgetItem(p.TITLE, self.categories)
            item.setIcon(_icon(p.ICON))
        self.categories.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.categories.setCurrentRow(0)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel, self)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self.restore_geometry(gprefs, 'epub_layout_fix_dialog_geometry')

    def accept(self):
        for p in self.panels:
            p.commit()
        self.save_geometry(gprefs, 'epub_layout_fix_dialog_geometry')
        QDialog.accept(self)

    def reject(self):
        self.save_geometry(gprefs, 'epub_layout_fix_dialog_geometry')
        QDialog.reject(self)


def make_convert_dialog(gui, db, book_id, preferred_input=None):
    """calibre's conversion window with our panels appended.

    ``calibre.gui2.convert.single.Config`` is internal API, so this is guarded: if calibre ever
    changes shape the caller falls back to :class:`FixOnlyDialog` plus a normal conversion rather
    than failing outright.
    """
    from calibre.gui2.convert.single import Config, GroupModel

    class ConvertAndFixDialog(Config):

        def setup_pipeline(self, *args):
            Config.setup_pipeline(self, *args)
            # setup_pipeline re-runs whenever the input/output format combo changes, so this
            # must rebuild rather than append blindly.
            self.widgets = [w for w in self.widgets
                            if not isinstance(w, (LayoutFixWidget, PolishWidget))]
            self.plugin_panels = [PolishWidget(self.stack if hasattr(self, 'stack') else self),
                                  LayoutFixWidget(self.stack if hasattr(self, 'stack') else self)]
            self.widgets.extend(self.plugin_panels)
            self._groups_model = GroupModel(self.widgets)
            self.groups.setModel(self._groups_model)
            try:
                self.groups.setCurrentIndex(self._groups_model.index(0))
            except Exception:                                  # noqa: BLE001
                pass

    return ConvertAndFixDialog(gui, db, book_id, preferred_input_format=preferred_input,
                               preferred_output_format='EPUB')
