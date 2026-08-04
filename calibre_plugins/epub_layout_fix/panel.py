#!/usr/bin/env python3
"""The two panels this plugin contributes.

Both subclass ``calibre.gui2.convert.Widget`` so they drop straight into calibre's own conversion
window and inherit its look, the left-hand category entry, the help pane and Restore defaults.
They are equally usable inside the plugin's own compact dialog.

* :class:`LayoutFixWidget` - the image and cover repairs
* :class:`PolishWidget`    - calibre's polish operations, driven by calibre's own option list so
  the set stays correct across calibre versions instead of drifting from a hardcoded copy
"""

from __future__ import annotations

try:
    from qt.core import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                         QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, Qt)
except ImportError:                                            # older calibre
    from PyQt5.Qt import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                          QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, Qt)

from calibre.gui2.convert import Widget

from calibre_plugins.epub_layout_fix.config import EPUB_VERSIONS, prefs

__license__ = 'GPL v3'


class _PluginWidget(Widget):
    """Common plumbing for a panel whose settings live in our JSONConfig rather than in the
    conversion pipeline.

    ``Widget`` normally maps ``opt_*`` attributes onto conversion options. We pass no options and
    override the three hooks calibre calls, so the panel behaves correctly in the conversion
    window without pretending to be a conversion option group.
    """

    COMMIT_NAME = None

    def __init__(self, parent):
        Widget.__init__(self, parent, [])
        self.build()
        self.load()

    # -- hooks called by calibre.gui2.convert.single.Config ------------------------------
    def initialize_options(self, get_option, get_help, db=None, book_id=None):
        self.load()

    def pre_commit_check(self):
        return True

    def commit(self, save_defaults=False):
        self.save()
        return True

    def commit_options(self, save_defaults=False):
        self.save()
        return True

    def restore_defaults(self, get_option=None):
        for key in self.KEYS:
            prefs[key] = prefs.defaults[key]
        self.load()

    def apply_recommendations(self, recs):
        pass

    def setup_help(self, help_provider):
        pass

    # -- to implement -------------------------------------------------------------------
    KEYS = ()

    def build(self):
        raise NotImplementedError

    def load(self):
        raise NotImplementedError

    def save(self):
        raise NotImplementedError


class LayoutFixWidget(_PluginWidget):
    """The image and cover repairs."""

    TITLE = _('Layout fixes')
    ICON = 'format-fill-color.png'
    HELP = _('Repair full-page images and covers so they fit the screen')

    KEYS = ('fix_images', 'min_width_percent', 'fix_covers', 'dark_cover', 'cover_color',
            'preserve_anchors', 'target_epub_version')

    def build(self):
        layout = QVBoxLayout(self)

        images = QGroupBox(_('Full-page images'))
        form = QFormLayout(images)
        self.fix_images = QCheckBox(_('Rewrite full-page images so they fit the page'))
        self.fix_images.setToolTip(_(
            'Qualifying pages are rebuilt as an SVG page object, which fits the image in both '
            'dimensions. This repairs images clipped at the edge as well as images that overrun '
            'the bottom of the page.'))
        form.addRow(self.fix_images)

        self.min_width = QDoubleSpinBox()
        self.min_width.setRange(0.0, 100.0)
        self.min_width.setDecimals(1)
        self.min_width.setSuffix(' %')
        self.min_width.setToolTip(_(
            'How wide an image must actually be displayed, relative to the column, to count as '
            'a full-page image. Measured across a real library, genuine full-page art resolves '
            'to 90-100% while deliberate ornaments sit near 40%.'))
        form.addRow(_('Treat as full-page from:'), self.min_width)

        self.preserve_anchors = QCheckBox(_('Preserve anchor ids on rewritten pages'))
        self.preserve_anchors.setToolTip(_(
            'The table of contents and page list can link to "page.xhtml#anchor". Rewriting a '
            'page would destroy those targets, so the ids are carried across. Leave this on.'))
        form.addRow(self.preserve_anchors)
        layout.addWidget(images)

        cover = QGroupBox(_('Cover'))
        cform = QFormLayout(cover)
        self.fix_covers = QCheckBox(_('Repair stretched covers'))
        self.fix_covers.setToolTip(_(
            'calibre writes preserveAspectRatio="none" into its generated title page unless '
            '"Preserve cover aspect ratio" is enabled, which distorts the cover.'))
        cform.addRow(self.fix_covers)

        self.dark_cover = QCheckBox(_('Dark letterbox bands around the cover'))
        cform.addRow(self.dark_cover)

        row = QHBoxLayout()
        self.cover_color = QLineEdit()
        self.cover_color.setMaxLength(7)
        self.cover_color.setFixedWidth(90)
        row.addWidget(self.cover_color)
        pick = QPushButton(_('Choose...'))
        pick.clicked.connect(self.pick_colour)
        row.addWidget(pick)
        row.addStretch(1)
        cform.addRow(_('Letterbox colour:'), row)
        layout.addWidget(cover)

        out = QGroupBox(_('Output'))
        oform = QFormLayout(out)
        self.epub_version = QComboBox()
        for value, label in EPUB_VERSIONS:
            self.epub_version.addItem(label, value)
        self.epub_version.setToolTip(_(
            'EPUB 3 upgrades the book internals first, using calibre\'s own "Upgrade book '
            'internals" step. Choose EPUB 2 to leave the book at its current version; the '
            'EPUB 3-only properties="svg" manifest attribute is then not written.'))
        oform.addRow(_('Target EPUB version:'), self.epub_version)
        layout.addWidget(out)

        note = QLabel(_(
            'Content pages are never given a dark background: the reader draws its header and '
            'footer text on them in black, which would become unreadable.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def pick_colour(self):
        try:
            from qt.core import QColor, QColorDialog
        except ImportError:
            from PyQt5.Qt import QColor, QColorDialog
        c = QColorDialog.getColor(QColor(self.cover_color.text() or '#000000'), self,
                                  _('Letterbox colour'))
        if c.isValid():
            self.cover_color.setText(c.name())

    def load(self):
        self.fix_images.setChecked(bool(prefs['fix_images']))
        self.min_width.setValue(float(prefs['min_width_percent']))
        self.preserve_anchors.setChecked(bool(prefs['preserve_anchors']))
        self.fix_covers.setChecked(bool(prefs['fix_covers']))
        self.dark_cover.setChecked(bool(prefs['dark_cover']))
        self.cover_color.setText(str(prefs['cover_color']))
        want = str(prefs.get('target_epub_version', '3'))
        idx = self.epub_version.findData(want)
        self.epub_version.setCurrentIndex(idx if idx >= 0 else 0)

    def save(self):
        import re
        prefs['fix_images'] = self.fix_images.isChecked()
        prefs['min_width_percent'] = float(self.min_width.value())
        prefs['preserve_anchors'] = self.preserve_anchors.isChecked()
        prefs['fix_covers'] = self.fix_covers.isChecked()
        prefs['dark_cover'] = self.dark_cover.isChecked()
        colour = (self.cover_color.text() or '').strip()
        prefs['cover_color'] = colour if re.match(r'^#[0-9A-Fa-f]{6}$', colour) else '#000000'
        prefs['target_epub_version'] = self.epub_version.currentData() or '3'


#: Labels for calibre's polish operations. The *set* of operations comes from calibre at runtime
#: (see :func:`polish_operations`) so a newly added one still appears; this only supplies nicer
#: text where we have it, matching calibre's own wording.
POLISH_LABELS = {
    'subset': _('Subset all embedded fonts'),
    'embed': _('Embed referenced fonts'),
    'smarten_punctuation': _('Smarten punctuation'),
    'jacket': _('Add/replace metadata as a "book jacket" page'),
    'remove_jacket': _('Remove a previously inserted book jacket'),
    'remove_unused_css': _('Remove unused CSS rules from the book'),
    'compress_images': _('Losslessly compress images'),
    'add_soft_hyphens': _('Add soft hyphens'),
    'remove_soft_hyphens': _('Remove soft hyphens'),
    'upgrade_book': _('Upgrade book internals (EPUB 2 to EPUB 3)'),
    'download_external_resources': _('Download external resources referenced by URL'),
    'remove_unused_images': _('Remove unused images'),
}

#: Not offered: these need a file argument rather than a checkbox, and the plugin supplies the
#: library's own cover/OPF automatically when the corresponding option is enabled elsewhere.
POLISH_SKIP = frozenset({'cover', 'opf'})


def polish_operations():
    """calibre's polish operations, as ``[(key, label, help_text)]``."""
    try:
        from calibre.ebooks.oeb.polish.main import ALL_OPTS, HELP
    except ImportError:
        return []
    out = []
    for key in sorted(ALL_OPTS):
        if key in POLISH_SKIP:
            continue
        label = POLISH_LABELS.get(key, key.replace('_', ' ').capitalize())
        help_text = HELP.get(key, '')
        if help_text:
            import re as _re
            help_text = _re.sub(r'<[^>]+>', '', help_text).strip()
        out.append((key, label, help_text))
    return out


class PolishWidget(_PluginWidget):
    """calibre's Polish book operations, offered inside our window.

    Defaults are seeded from calibre's own saved polish settings the first time, so the panel
    opens matching what Polish book would already do.
    """

    TITLE = _('Polish')
    ICON = 'polish.png'
    HELP = _('Run calibre\'s polishing operations before the layout fixes')

    KEYS = ('polish_enabled', 'polish_ops')

    def build(self):
        layout = QVBoxLayout(self)

        self.enabled = QCheckBox(_('Polish the book before applying the layout fixes'))
        self.enabled.setToolTip(_(
            'Polishing runs first so the layout fixes always have the final say on the pages '
            'they own.'))
        layout.addWidget(self.enabled)

        self.ops_box = QGroupBox(_('Operations'))
        form = QVBoxLayout(self.ops_box)
        self.boxes = {}
        for key, label, help_text in polish_operations():
            cb = QCheckBox(label)
            if help_text:
                cb.setToolTip(help_text)
            form.addWidget(cb)
            self.boxes[key] = cb
        layout.addWidget(self.ops_box)

        row = QHBoxLayout()
        for text, slot in ((_('Select &all'), lambda: self._set_all(True)),
                           (_('Select &none'), lambda: self._set_all(False)),
                           (_('Use calibre\'s settings'), self._load_calibre_defaults)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        warn = QLabel(_(
            'Note: "Download external resources" fetches remote URLs from the internet, and '
            '"Embed referenced fonts" scans this computer for matching fonts.'))
        warn.setWordWrap(True)
        layout.addWidget(warn)

        self.enabled.toggled.connect(self.ops_box.setEnabled)
        layout.addStretch(1)

    def _set_all(self, state):
        for cb in self.boxes.values():
            cb.setChecked(bool(state))

    def _load_calibre_defaults(self):
        for key, value in calibre_polish_defaults().items():
            if key in self.boxes:
                self.boxes[key].setChecked(bool(value))

    def load(self):
        self.enabled.setChecked(bool(prefs.get('polish_enabled', True)))
        stored = prefs.get('polish_ops', None)
        if not stored:
            stored = calibre_polish_defaults()
            stored.setdefault('upgrade_book', True)     # EPUB 3 by default
        for key, cb in self.boxes.items():
            cb.setChecked(bool(stored.get(key, False)))
        self.ops_box.setEnabled(self.enabled.isChecked())

    def save(self):
        prefs['polish_enabled'] = self.enabled.isChecked()
        prefs['polish_ops'] = {k: cb.isChecked() for k, cb in self.boxes.items()}


def calibre_polish_defaults():
    """Whatever the user already has saved in calibre's own Polish book dialog."""
    try:
        from calibre.gui2 import gprefs
        saved = gprefs.get('polishing_settings') or {}
        return dict(saved)
    except Exception:                                          # noqa: BLE001
        return {}
