#!/usr/bin/env python3
"""The toolbar actions.

Three separate InterfaceActions so each can be placed independently in
Preferences -> Toolbars & menus. The main one also carries a drop-down with all of them.
"""

from __future__ import annotations

import os

try:
    from qt.core import QMenu
except ImportError:                                            # older calibre
    from PyQt5.Qt import QMenu

from calibre.gui2 import Dispatcher, error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob
from calibre.ptempfile import PersistentTemporaryFile

from calibre_plugins.epub_layout_fix.config import (current_settings, polish_settings,
                                                    target_epub_version)

__license__ = 'GPL v3'

ICON = 'format-fill-color.png'


class _BaseGui(InterfaceAction):
    """Shared selection handling, job dispatch and result reporting."""

    action_type = 'current'
    dont_add_to = frozenset()

    def genesis(self):
        self.qaction.triggered.connect(self.run)

    # -- selection ----------------------------------------------------------------------
    def selected_book_ids(self):
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return []
        return list(map(self.gui.library_view.model().id, rows))

    def _db(self):
        return self.gui.current_db.new_api

    def _require_selection(self):
        ids = self.selected_book_ids()
        if not ids:
            error_dialog(self.gui, _('No books selected'),
                         _('Select the books to process first.'), show=True)
            return None
        return ids

    # -- job plumbing -------------------------------------------------------------------
    def _start(self, jobs, description):
        if not jobs:
            return
        from calibre_plugins.epub_layout_fix.jobs import run_batch
        job = ThreadedJob('epub_layout_fix', description, run_batch, (jobs,), {},
                          Dispatcher(self._finished))
        self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message(description, 3000)

    def _finished(self, job):
        if job.failed:
            return self.gui.job_exception(job, dialog_title=_('Layout fix failed'))
        results = job.result or []
        self._commit_results(results)
        self._report(results)

    def _commit_results(self, results):
        """Write changed files back into the library. Runs on the GUI thread."""
        db = self._db()
        for r in results:
            book_id = r.get('book_id')
            if book_id is None or r.get('error') or not r.get('changed'):
                continue
            try:
                # same call calibre's own Polish action makes, so its Restore original works
                db.save_original_format(book_id, 'EPUB')
            except Exception:                                  # noqa: BLE001 - no ORIGINAL yet
                pass
            try:
                with open(r['path'], 'rb') as f:
                    db.add_format(book_id, 'EPUB', f, run_hooks=False)
            except Exception as e:                             # noqa: BLE001
                r['error'] = _('could not add the fixed EPUB back: %s') % e
            finally:
                try:
                    os.remove(r['path'])
                except OSError:
                    pass

    def _report(self, results):
        changed = [r for r in results if r.get('changed') and not r.get('error')]
        failed = [r for r in results if r.get('error')]
        images = sum(r.get('image_pages', 0) for r in results)
        svgs = sum(r.get('svg_repaired', 0) for r in results)
        covers = sum(1 for r in results if r.get('cover_fixed'))

        lines = [
            _('Books processed: %d') % len(results),
            _('Books changed: %d') % len(changed),
            _('Full-page images rewritten: %d') % images,
            _('Stretched SVG/cover repairs: %d') % svgs,
            _('Covers given dark letterbox bands: %d') % covers,
        ]
        if failed:
            lines.append('')
            lines.append(_('Failed: %d') % len(failed))
            for r in failed[:20]:
                lines.append('   %s: %s' % (r.get('title') or r['name'], r['error']))

        detail = []
        for r in results:
            if r.get('details') or r.get('problems'):
                detail.append(r.get('title') or r['name'])
                detail.extend('    ' + d for d in r.get('details', []))
                detail.extend('    !! ' + p for p in r.get('problems', []))

        info_dialog(self.gui, _('Layout fix complete'), '\n'.join(lines),
                    det_msg='\n'.join(detail) or None, show=True)

    # -- building jobs ------------------------------------------------------------------
    def _epub_jobs(self, book_ids):
        """One job per selected book that actually has an EPUB."""
        db = self._db()
        jobs, missing = [], []
        settings = current_settings()
        polish_on, polish_ops = polish_settings()
        for book_id in book_ids:
            fmts = {f.upper() for f in (db.formats(book_id) or ())}
            if 'EPUB' not in fmts:
                missing.append(book_id)
                continue
            pt = PersistentTemporaryFile('.epub')
            pt.close()
            db.copy_format_to(book_id, 'EPUB', pt.name)
            jobs.append({
                'book_id': book_id,
                'title': db.field_for('title', book_id),
                'path': pt.name,
                'settings': settings,
                'polish_ops': polish_ops if polish_on else None,
                'target_version': target_epub_version(),
            })
        return jobs, missing


class FixLayoutQuickGui(_BaseGui):
    """Run immediately with the stored settings."""

    name = 'EPUB Layout Fix - quick run'
    action_spec = (_('Fix layout'), ICON,
                   _('Repair full-page images and covers using the stored settings'), None)

    def run(self):
        ids = self._require_selection()
        if not ids:
            return
        jobs, missing = self._epub_jobs(ids)
        if missing and not jobs:
            return error_dialog(self.gui, _('No EPUB'),
                                _('None of the selected books has an EPUB format.'), show=True)
        self._start(jobs, _('Fixing layout of %d book(s)') % len(jobs))


class FixLayoutGui(_BaseGui):
    """Show the settings window, then run."""

    name = 'EPUB Layout Fix'
    action_spec = (_('Fix layout...'), ICON,
                   _('Choose the repairs to apply, then fix the selected books'), None)
    action_add_menu = True

    def genesis(self):
        self.qaction.triggered.connect(self.run)
        m = self.qaction.menu() or QMenu(self.gui)
        self.qaction.setMenu(m)
        self.create_menu_action(m, 'eplf_fix_dialog', _('Fix layout...'),
                                icon=ICON, triggered=self.run)
        self.create_menu_action(m, 'eplf_fix_quick', _('Fix layout (last settings)'),
                                icon=ICON, triggered=self.run_quick)
        self.create_menu_action(m, 'eplf_convert', _('Convert to EPUB and fix...'),
                                icon=ICON, triggered=self.run_convert)
        m.addSeparator()
        self.create_menu_action(m, 'eplf_settings', _('Settings...'),
                                icon='config.png', triggered=self.show_settings)

    def show_settings(self):
        self.interface_action_base_plugin.do_user_config(self.gui)

    def run_quick(self):
        ids = self._require_selection()
        if not ids:
            return
        jobs, _missing = self._epub_jobs(ids)
        self._start(jobs, _('Fixing layout of %d book(s)') % len(jobs))

    def run_convert(self):
        act = self.gui.iactions.get('EPUB Layout Fix - convert and fix')
        if act is not None:
            return act.run()
        error_dialog(self.gui, _('Not available'),
                     _('The "Convert to EPUB and fix layout" action is not enabled.'), show=True)

    def run(self):
        ids = self._require_selection()
        if not ids:
            return
        from calibre_plugins.epub_layout_fix.dialog import FixOnlyDialog
        d = FixOnlyDialog(self.gui)
        if not d.exec():
            return
        jobs, missing = self._epub_jobs(ids)
        if missing and not jobs:
            return error_dialog(self.gui, _('No EPUB'),
                                _('None of the selected books has an EPUB format.'), show=True)
        if missing and not question_dialog(
                self.gui, _('Some books have no EPUB'),
                _('%d of the selected books have no EPUB format and will be skipped. Continue?')
                % len(missing)):
            return
        self._start(jobs, _('Fixing layout of %d book(s)') % len(jobs))


class ConvertAndFixGui(_BaseGui):
    """calibre's conversion window with our panels, then convert -> polish -> fix."""

    name = 'EPUB Layout Fix - convert and fix'
    action_spec = (_('Convert to EPUB and fix...'), ICON,
                   _('Convert the selected books to EPUB with the layout fixes applied'), None)

    def run(self):
        ids = self._require_selection()
        if not ids:
            return
        db = self._db()

        try:
            from calibre_plugins.epub_layout_fix.dialog import make_convert_dialog
            d = make_convert_dialog(self.gui, self.gui.current_db, ids[0])
        except Exception as e:                                 # noqa: BLE001
            return error_dialog(
                self.gui, _('Conversion window unavailable'),
                _('This calibre version does not expose the conversion window in the expected '
                  'way, so the combined window cannot be shown.\n\n'
                  'Use "Fix layout..." after converting normally.'),
                det_msg=str(e), show=True)

        if not d.exec():
            return
        recs = list(getattr(d, 'recommendations', []) or [])

        settings = current_settings()
        polish_on, polish_ops = polish_settings()
        jobs = []
        for book_id in ids:
            fmts = {f.upper() for f in (db.formats(book_id) or ())}
            src_fmt = self._pick_source(fmts, getattr(d, 'input_format', None))
            if not src_fmt:
                continue
            src = PersistentTemporaryFile('.' + src_fmt.lower())
            src.close()
            db.copy_format_to(book_id, src_fmt, src.name)
            out = PersistentTemporaryFile('.epub')
            out.close()
            jobs.append({
                'book_id': book_id,
                'title': db.field_for('title', book_id),
                'path': out.name,
                'convert_from': src.name,
                'recommendations': recs,
                'settings': settings,
                'polish_ops': polish_ops if polish_on else None,
                'target_version': target_epub_version(),
            })
        if not jobs:
            return error_dialog(self.gui, _('Nothing to convert'),
                                _('None of the selected books has a convertible format.'),
                                show=True)
        self._start(jobs, _('Converting and fixing %d book(s)') % len(jobs))

    @staticmethod
    def _pick_source(fmts, preferred=None):
        if preferred and preferred.upper() in fmts:
            return preferred.upper()
        for f in ('KFX', 'AZW3', 'MOBI', 'AZW', 'EPUB', 'KEPUB', 'DOCX', 'FB2', 'PDF'):
            if f in fmts:
                return f
        return next(iter(fmts)) if fmts else None
