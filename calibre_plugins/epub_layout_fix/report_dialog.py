#!/usr/bin/env python3
"""The report window.

Shows what a run would do without doing any of it. The engine has always planned every change
before writing (:func:`fixer.analyze_epub`), and every page it looks at leaves a ledger entry
saying what happened to it and why. This is where both finally surface.
"""

from __future__ import annotations

import os

try:
    from qt.core import (QApplication, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                         QPushButton, QHBoxLayout, Qt, QTreeWidget, QTreeWidgetItem,
                         QVBoxLayout)
except ImportError:                                            # older calibre
    from PyQt5.Qt import (QApplication, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                          QPushButton, QHBoxLayout, Qt, QTreeWidget, QTreeWidgetItem,
                          QVBoxLayout)

from calibre.gui2 import choose_save_file, error_dialog, gprefs, info_dialog

__license__ = 'GPL v3'

COLUMNS = (_('Book / page'), _('Action'), _('Category'), _('Size'), _('Reason'))


def would_change(result):
    return bool(result.get('changed'))


class ReportDialog(QDialog):
    """One row per book, expanding to one row per page examined."""

    def __init__(self, parent, results):
        QDialog.__init__(self, parent)
        self.setWindowTitle(_('What would change'))
        self.results = results

        outer = QVBoxLayout(self)

        needing = [r for r in results if would_change(r) and not r.get('error')]
        failed = [r for r in results if r.get('error')]
        head = QLabel(_('<b>%(n)d of %(t)d book(s) would be changed.</b> Nothing has been '
                        'written - this is a report only.')
                      % {'n': len(needing), 't': len(results)})
        head.setWordWrap(True)
        outer.addWidget(head)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        outer.addWidget(self.tree, 1)

        for r in results:
            title = r.get('title') or r.get('name') or ''
            if r.get('error'):
                summary = _('could not be read: %s') % r['error']
            elif would_change(r):
                summary = _('%(i)d image page(s), %(s)d svg repair(s), cover %(c)s, '
                            '%(d)d dead link(s), %(k)d skipped')
                summary = summary % {'i': r.get('image_pages', 0),
                                     's': r.get('svg_repaired', 0),
                                     'c': _('fixed') if r.get('cover_fixed') else _('unchanged'),
                                     'd': r.get('dead_links', 0), 'k': r.get('skipped', 0)}
            else:
                summary = _('nothing to do')

            top = QTreeWidgetItem(self.tree, [title, '', '', '', summary])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)

            for entry in r.get('ledger') or ():
                w, h = entry.get('width'), entry.get('height')
                size = '%dx%d' % (w, h) if w and h else ''
                QTreeWidgetItem(top, [entry.get('page', ''), entry.get('action', ''),
                                      entry.get('category', ''), size, entry.get('reason', '')])
            top.setExpanded(would_change(r) and len(r.get('ledger') or ()) <= 20)

        for i in range(len(COLUMNS) - 1):
            self.tree.resizeColumnToContents(i)
        self.tree.header().setSectionResizeMode(len(COLUMNS) - 1,
                                                QHeaderView.ResizeMode.Stretch)

        row = QHBoxLayout()
        if failed:
            row.addWidget(QLabel(_('%d book(s) could not be read.') % len(failed)))
        row.addStretch(1)
        csv = QPushButton(_('Export &CSV...'), self)
        csv.setToolTip(_('Write every row below to a file, one line per page examined.'))
        csv.clicked.connect(self.export_csv)
        row.addWidget(csv)
        outer.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        outer.addWidget(bb)

        self.restore_geometry(gprefs, 'epub_layout_fix_report_geometry')

    def rows(self):
        """Every ledger entry across every book, flattened for export."""
        out = []
        for r in self.results:
            title = r.get('title') or r.get('name') or ''
            if not r.get('ledger'):
                out.append([title, '', r.get('error') and 'error' or 'no-change', '', '', '',
                            r.get('error') or ''])
                continue
            for e in r['ledger']:
                out.append([title, e.get('page', ''), e.get('action', ''),
                            e.get('category', ''), e.get('width') or '',
                            e.get('height') or '', e.get('reason', '')])
        return out

    def export_csv(self):
        import csv

        path = choose_save_file(self, 'eplf-report-csv', _('Save the report'),
                                filters=[(_('CSV files'), ['csv'])],
                                initial_filename='epub-layout-fix-report.csv')
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['book', 'page', 'action', 'category', 'width', 'height',
                                 'reason'])
                writer.writerows(self.rows())
        except OSError as e:
            return error_dialog(self, _('Could not save'), str(e), show=True)
        info_dialog(self, _('Saved'), _('%(n)d row(s) written to %(p)s')
                    % {'n': len(self.rows()), 'p': os.path.basename(path)}, show=True)

    def accept(self):
        self.save_geometry(gprefs, 'epub_layout_fix_report_geometry')
        QDialog.accept(self)

    def reject(self):
        self.save_geometry(gprefs, 'epub_layout_fix_report_geometry')
        QDialog.reject(self)
