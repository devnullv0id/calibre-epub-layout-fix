#!/usr/bin/env python3
"""The windows.

:func:`make_convert_dialog` is calibre's own single-book conversion window with our panels
appended, so it carries the full Metadata / Look & feel / Page setup / ... set unchanged and adds
Polish and Layout fixes categories.

:func:`make_bulk_convert_dialog` is the same treatment for *Bulk convert N books*, which calibre
shows instead whenever more than one book is selected. It offers a deliberately smaller set of
panels - no Metadata, no Debug, no input format - because those are per-book choices that cannot
sensibly be shared, and it adds the "use saved conversion settings for individual books" option.

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


def install_panels(dialog, group_model):
    """Put our two panels at the end of the dialog's category list.

    ``setup_pipeline`` re-runs whenever a format combo changes, so this rebuilds the list rather
    than appending blindly - otherwise the panels would multiply on every re-run.

    Both calibre windows build ``self.widgets`` and then hand it to a ``GroupModel``, so the same
    treatment works for the single and the bulk window; only the module the model comes from
    differs, hence the argument.
    """
    dialog.widgets = [w for w in dialog.widgets
                      if not isinstance(w, (LayoutFixWidget, PolishWidget))]
    dialog.plugin_panels = [PolishWidget(dialog), LayoutFixWidget(dialog)]
    dialog.widgets.extend(dialog.plugin_panels)

    dialog._groups_model = group_model(dialog.widgets)
    dialog.groups.setModel(dialog._groups_model)


def force_epub_output(dialog):
    """The repairs only apply to EPUB, so do not offer an output format that cannot receive them."""
    try:
        combo = dialog.output_formats
        combo.blockSignals(True)          # changing this re-triggers setup_pipeline
        combo.clear()
        combo.addItem('EPUB')
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        combo.setEnabled(False)
        combo.setToolTip(_('This plugin only repairs EPUB, so the output format is fixed.'))
    except Exception:                                          # noqa: BLE001 - cosmetic only
        pass


def make_convert_dialog(gui, db, book_id, preferred_input=None):
    """calibre's single-book conversion window with our panels appended.

    ``calibre.gui2.convert.single.Config`` is internal API, so this is guarded: if calibre ever
    changes shape the caller falls back to :class:`FixOnlyDialog` plus a normal conversion rather
    than failing outright.
    """
    from calibre.gui2.convert.single import Config, GroupModel

    class ConvertAndFixDialog(Config):

        def setup_pipeline(self, *args):
            Config.setup_pipeline(self, *args)
            install_panels(self, GroupModel)

            # setModel gives the view a brand new selection model, which orphans the connection
            # calibre made inside setup_pipeline - without this the category list highlights but
            # the pane never changes. The bulk window drives its panes from the view's own
            # signals instead, so it needs no equivalent.
            self.groups.selectionModel().currentChanged.connect(self.current_group_changed)
            self.groups.setCurrentIndex(self._groups_model.index(0))
            self.show_pane(0)

    d = ConvertAndFixDialog(gui, db, book_id, preferred_input_format=preferred_input,
                            preferred_output_format='EPUB')
    force_epub_output(d)
    return d


def make_bulk_convert_dialog(gui, db, book_ids):
    """calibre's *Bulk convert N books* window with our panels appended.

    calibre picks this window itself whenever more than one book is selected, and the difference
    is not only cosmetic: the single-book window offers per-book choices - Metadata, and a
    Search & replace written against one book's text - which the plugin was silently applying to
    every selected book.

    Also internal API, and guarded the same way.
    """
    from calibre.gui2.convert.bulk import BulkConfig
    from calibre.gui2.convert.single import GroupModel

    ids = list(book_ids)

    # Drives the "use saved conversion settings for individual books" checkbox: calibre disables
    # it, with an explanatory tooltip, when none of the selected books has anything saved.
    try:
        has_saved = bool(db.has_conversion_options(ids))
    except Exception:                                          # noqa: BLE001 - assume it might
        has_saved = True

    class BulkConvertAndFixDialog(BulkConfig):

        def setup_pipeline(self, *args):
            BulkConfig.setup_pipeline(self, *args)
            install_panels(self, GroupModel)
            self.groups.setCurrentIndex(self._groups_model.index(0))
            self.show_pane(0)

    d = BulkConvertAndFixDialog(gui, db, preferred_output_format='EPUB',
                                has_saved_settings=has_saved, book_ids=ids)
    force_epub_output(d)
    return d
