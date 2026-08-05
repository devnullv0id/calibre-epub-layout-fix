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

from calibre_plugins.epub_layout_fix import jobs as jobs_module
from calibre_plugins.epub_layout_fix.config import (auto_settings, beautify_enabled,
                                                    current_settings, dry_run_enabled,
                                                    polish_settings,
                                                    target_epub_version)

__license__ = 'GPL v3'

ICON = 'format-fill-color.png'

# Re-exported: they live in jobs.py so the command line, which must not import a Qt widget, can
# pick a source format the same way the toolbar does.
SOURCE_PREFERENCE = jobs_module.SOURCE_PREFERENCE
pick_source = jobs_module.pick_source


def bulk_recommendations(db, dialog):
    """-> ``f(book_id, src_fmt)`` giving that book's conversion recommendations.

    The bulk window carries one set of options for the whole selection plus a "use saved
    conversion settings for individual books" checkbox, so the options are not the same for every
    book. The layering matches ``calibre.gui2.tools.QueueBulk.do_book``: the input format's own
    bulk defaults first, then the book's saved settings when the box is ticked, then the window's
    settings on top.

    Unlike calibre, this does not call ``save_specifics`` afterwards - the plugin reads the
    conversion settings stored for a book, it does not rewrite them.
    """
    from calibre.ebooks.conversion.config import GuiRecommendations, load_specifics
    from calibre.gui2.convert import bulk_defaults_for_input_format

    # Config.recommendations is a list of (name, value, level) triples, not a mapping
    window_recs = {r[0]: r[1] for r in (getattr(dialog, 'recommendations', None) or ())}
    try:
        use_saved = bool(dialog.opt_individual_saved_settings.isChecked())
    except Exception:                                          # noqa: BLE001 - checkbox hidden
        use_saved = False

    def recommendations_for(book_id, src_fmt):
        combined = GuiRecommendations()
        try:
            combined.update(bulk_defaults_for_input_format((src_fmt or '').lower()))
        except Exception:                                      # noqa: BLE001 - unknown input
            pass
        if use_saved:
            try:
                combined.update(load_specifics(db, book_id) or {})
            except Exception:                                  # noqa: BLE001 - nothing saved
                pass
        combined.update(window_recs)
        # LOW is what calibre's own bulk path uses; the recommendations the plugin forces
        # afterwards - the metadata OPF, preserve_cover_aspect_ratio, epub_version - are HIGH and
        # still win.
        return list(combined.to_recommendations())

    return recommendations_for


class _BaseGui(InterfaceAction):
    """Shared selection handling, job dispatch and result reporting."""

    action_type = 'current'
    dont_add_to = frozenset()

    #: None means "use the stored setting"; True/False force it for one run
    dry_run = None

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
    def _start(self, jobs, description, silent=False, worker=None, kind='fix'):
        """Queue one job per book, the way calibre's own Convert action does.

        A single batch job gives no per-book progress and cannot be cancelled selectively, so
        each book gets its own entry in the job list and results are collected as they land.

        ``silent`` suppresses the completion dialog when nothing was changed and nothing failed -
        used by the automatic run, which must not interrupt an import that had no work in it.

        ``worker`` and ``kind`` let the report action reuse this plumbing for a pass that only
        looks; the default is the layout fix.
        """
        if not jobs:
            return
        from functools import partial

        from calibre_plugins.epub_layout_fix.jobs import run_single

        worker = worker or run_single
        batch = {'total': len(jobs), 'results': [], 'done': 0, 'silent': silent,
                 'kind': kind, 'description': description}
        total = len(jobs)
        for i, spec in enumerate(jobs, 1):
            title = spec.get('title') or os.path.basename(spec['path'])
            verb = _('Examine') if kind == 'report' else (
                _('Convert and fix') if spec.get('convert_from') else _('Fix layout'))
            if spec.get('dry_run'):
                verb = _('Dry run:') + ' ' + verb.lower()
            desc = (_('%(verb)s book %(i)d of %(n)d (%(t)s)')
                    % {'verb': verb, 'i': i, 'n': total, 't': title})
            job = ThreadedJob('epub_layout_fix', desc, worker, (spec,), {},
                              Dispatcher(partial(self._one_finished, batch)))
            self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message(description, 3000)

    def _one_finished(self, batch, job):
        """Called per book. Commits that book, and reports once the batch is complete."""
        if job.failed:
            batch['results'].append({
                'book_id': None, 'name': job.description, 'title': job.description,
                'error': _('job failed - see the job details'), 'changed': False,
                'image_pages': 0, 'svg_repaired': 0, 'cover_fixed': False, 'skipped': 0,
                'dead_links': 0,
                'details': [], 'ledger': [], 'problems': [],
            })
            self.gui.job_exception(job, dialog_title=_('Layout fix failed'), retry_func=None)
        else:
            result = job.result
            if result:
                self._commit_results([result])
                batch['results'].append(result)
                if result.get('dry_run'):
                    self._mark_dry(job)

        # Count completions, not results: a job that returns nothing still finished, and counting
        # results would leave the batch permanently one short and never report.
        batch['done'] += 1
        if batch['done'] >= batch['total']:
            self._report(batch['results'], silent=batch.get('silent', False),
                         kind=batch.get('kind', 'fix'))

    #: the text the flagged books are marked with, so they can be found with marked:needs-fix
    MARK_LABEL = 'needs-fix'

    def _flag_books(self, book_ids, label=None):
        """Put a red pin against every book that still needs work.

        Uses calibre's own marked-books mechanism, the one Extract ISBN uses, so the flag shows
        in the row margin and ``marked:needs-fix`` finds them. Marks made by anything else are
        left alone; only our own label is refreshed, so a second run cannot leave stale pins
        behind or wipe another plugin's.

        The colour is normally picked per label from calibre's palette. Seeding the model's icon
        cache first pins it to red. The cache holds ``(colour, QIcon)`` pairs, not bare icons -
        storing an icon on its own makes ``marked_text_icon_for`` raise inside ``headerData``,
        which silently costs every row its pin.
        """
        label = label or self.MARK_LABEL
        db = self.gui.current_db
        try:
            existing = dict(db.data.marked_ids)
        except Exception:                                      # noqa: BLE001 - no marks yet
            existing = {}

        keep = {bid: text for bid, text in existing.items() if text != label}
        keep.update({bid: label for bid in book_ids})

        try:
            model = self.gui.library_view.model()
            from calibre.gui2.library.models import render_pin
            try:
                from qt.core import QIcon
            except ImportError:
                from PyQt5.Qt import QIcon
            model.marked_text_icons[label] = ('red', QIcon(render_pin('red')))
        except Exception:                                      # noqa: BLE001 - colour is cosmetic
            pass

        try:
            db.data.set_marked_ids(keep)
            self._repaint_marks(list(existing) + list(book_ids))
        except Exception:                                      # noqa: BLE001 - never break a run
            import traceback
            traceback.print_exc()
            return 0

        return len(book_ids)

    def _offer_filter(self, count, label=None):
        """Ask before narrowing the library down to the flagged books.

        Filtering does make the flags easy to find, but it changes what the user is looking at,
        so it is never done unannounced. calibre's own "show this again" checkbox remembers the
        answer, so the question is asked once rather than after every run.
        """
        if not count:
            return False
        label = label or self.MARK_LABEL
        try:
            from calibre.gui2 import question_dialog
            ok = question_dialog(
                self.gui, _('Show only the flagged books?'),
                _('%(n)d book(s) are flagged with a red pin in the row margin.\n\n'
                  'Search the library for "marked:%(l)s" so that only those are listed? '
                  'Clearing the search box brings the whole library back.')
                % {'n': count, 'l': label},
                default_yes=False,
                skip_dialog_name='epub_layout_fix_filter_marked',
                skip_dialog_msg=_('Ask this again'),
                skip_dialog_skipped_value=False)
            if not ok:
                return False
            self.gui.search.set_search_string('marked:%s' % label)
            return True
        except Exception:                                      # noqa: BLE001 - cosmetic only
            return False

    def _repaint_marks(self, book_ids):
        """Make the pins actually appear.

        The pin is drawn by ``BooksModel.headerData`` for the *vertical* header, which
        ``refresh_ids`` does not touch - it invalidates row cells. Without an explicit
        ``headerDataChanged`` the marks are set, and searchable, while the margin still shows
        whatever it drew last.
        """
        model = self.gui.library_view.model()
        try:
            model.refresh_ids(list(book_ids))
        except Exception:                                      # noqa: BLE001 - ids off-view
            pass
        try:
            try:
                from qt.core import Qt
            except ImportError:
                from PyQt5.Qt import Qt
            rows = model.rowCount()
            if rows:
                model.headerDataChanged.emit(Qt.Orientation.Vertical, 0, rows - 1)
            view = self.gui.library_view
            view.verticalHeader().viewport().update()
            view.viewport().update()
        except Exception:                                      # noqa: BLE001 - cosmetic only
            pass

    def _unflag_books(self, book_ids):
        """Take our pin off books that no longer need it, leaving other marks alone."""
        done = set(book_ids)
        db = self.gui.current_db
        try:
            existing = dict(db.data.marked_ids)
        except Exception:                                      # noqa: BLE001
            return
        keep = {b: t for b, t in existing.items()
                if not (t == self.MARK_LABEL and b in done)}
        if keep == existing:
            return
        try:
            db.data.set_marked_ids(keep)
            self._repaint_marks(list(done))
        except Exception:                                      # noqa: BLE001
            return

        # If the view is still filtered to our pins and none are left, stop filtering rather
        # than leaving the user staring at an empty library.
        try:
            if str(self.gui.search.current_text or '').strip() == 'marked:%s' % self.MARK_LABEL:
                if not [b for b, t in keep.items() if t == self.MARK_LABEL]:
                    self.gui.search.set_search_string('')
        except Exception:                                      # noqa: BLE001 - cosmetic only
            pass

    @staticmethod
    def _mark_dry(job):
        """Say "discarded" rather than "Finished" once a dry-run job lands.

        ``BaseJob.status_text`` returns ``_message`` while a job runs - which the progress
        notifications set, so the stages already read "Dry run: polishing" - but switches to
        ``_status_text`` on completion, and calibre puts "Finished" there. A finished dry run
        claiming to be finished is the confusing half, so the text is replaced.
        """
        try:
            job._status_text = _('Dry run - discarded')
        except Exception:                                      # noqa: BLE001 - cosmetic only
            pass

    def _watcher(self):
        """The import watcher, which lives on the main action. May be None."""
        act = self.gui.iactions.get(FixLayoutGui.name)
        return getattr(act, 'watcher', None)

    def _commit_results(self, results):
        """Write changed files back into the library. Runs on the GUI thread."""
        db = self._db()
        written = []

        # Adding the format raises the very event the automatic run listens for. Tell the watcher
        # first, or it queues the book again the moment we finish with it.
        watcher = self._watcher()
        if watcher is not None:
            watcher.suppress([r.get('book_id') for r in results if r.get('book_id') is not None])
        for r in results:
            book_id = r.get('book_id')
            try:
                if book_id is None or r.get('error') or not r.get('changed'):
                    continue
                if r.get('dry_run'):
                    # the work was done and verified; it just does not get to stay
                    continue
                try:
                    # same call calibre's own Polish action makes, so its Restore original works
                    db.save_original_format(book_id, 'EPUB')
                except Exception:                              # noqa: BLE001 - no ORIGINAL yet
                    pass
                try:
                    with open(r['path'], 'rb') as f:
                        db.add_format(book_id, 'EPUB', f, run_hooks=False)
                    written.append(book_id)
                except Exception as e:                         # noqa: BLE001
                    r['error'] = _('could not add the fixed EPUB back: %s') % e
            finally:
                # Every book gets its temporary file removed, not just the ones that committed -
                # skipped and failed books used to leave theirs behind for the whole session.
                try:
                    os.remove(r['path'])
                except OSError:
                    pass
        self._refresh(written)

    def _refresh(self, book_ids):
        """Make the library view show the new format size straight away."""
        if not book_ids:
            return
        try:
            self.gui.library_view.model().refresh_ids(list(book_ids))
        except Exception:                                      # noqa: BLE001 - cosmetic only
            pass
        try:
            self.gui.refresh_cover_browser()
        except Exception:                                      # noqa: BLE001 - cosmetic only
            pass

    def _report(self, results, silent=False, kind='fix'):
        changed = [r for r in results if r.get('changed') and not r.get('error')]
        failed = [r for r in results if r.get('error')]
        images = sum(r.get('image_pages', 0) for r in results)
        svgs = sum(r.get('svg_repaired', 0) for r in results)
        covers = sum(1 for r in results if r.get('cover_fixed'))
        dead = sum(r.get('dead_links', 0) for r in results)

        dry = [r for r in results if r.get('dry_run')]
        lines = []
        if dry:
            lines.append(_('DRY RUN - nothing was written. %d of %d book(s) would have changed.')
                         % (len(changed), len(results)))
            lines.append('')
        lines += [
            _('Books processed: %d') % len(results),
            _('Books changed: %d') % len(changed),
            _('Full-page images rewritten: %d') % images,
            _('Stretched SVG/cover repairs: %d') % svgs,
            _('Covers given dark letterbox bands: %d') % covers,
            _('Dead references removed: %d') % dead,
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

        # A dry run leaves the books untouched, so whatever would have changed still needs work
        # and gets pinned. A real run has just fixed them, so the pin comes off.
        touched = [r['book_id'] for r in results
                   if r.get('book_id') is not None and not r.get('error')]
        need = [r['book_id'] for r in results
                if r.get('book_id') is not None and r.get('changed') and not r.get('error')]
        flagged = 0
        if dry:
            # No line about the pins here: the question that follows says the same thing, and
            # says it where it is actually useful.
            flagged = self._flag_books(need)
        else:
            self._unflag_books(touched)

        if silent and not changed and not failed:
            self.gui.status_bar.show_message(
                _('Layout fix: nothing to do for %d book(s)') % len(results), 5000)
            return

        info_dialog(self.gui, _('Dry run complete') if dry else _('Layout fix complete'),
                    '\n'.join(lines), det_msg='\n'.join(detail) or None, show=True)
        # Asked after the summary, so the numbers are read before the view changes under them.
        self._offer_filter(flagged)

    # -- building jobs ------------------------------------------------------------------
    def _metadata_recommendations(self, book_id):
        """``read_metadata_from_opf`` pointing at this book's library metadata.

        calibre's own Convert action writes the library's metadata to a temporary OPF and hands it
        to the pipeline (see ``calibre.gui2.tools.convert_single_ebook``). Without it the output
        carries whatever was embedded in the source file, which loses the display form of the
        author, the author order, the sort names and the ``calibre:<uuid>`` identifier that ties
        the file back to its library record.

        The cover is deliberately stripped from the metadata: supplying one makes calibre replace
        the publisher's own cover page with a regenerated title page, and it then leaves the
        navigation document pointing at the page it just deleted.
        """
        from calibre.customize.conversion import OptionRecommendation

        try:
            from calibre.ebooks.metadata.opf2 import metadata_to_opf
            mi = self._db().get_metadata(book_id)
            mi.cover = None
            mi.cover_data = (None, None)
            opf = PersistentTemporaryFile('.opf')
            data = metadata_to_opf(mi)
            opf.write(data if isinstance(data, bytes) else data.encode('utf-8'))
            opf.close()
        except Exception:                                      # noqa: BLE001 - metadata is a bonus
            import traceback
            traceback.print_exc()
            return []
        return [('read_metadata_from_opf', opf.name, OptionRecommendation.HIGH)]

    def _convert_jobs(self, book_ids, recs=(), preferred=None, automatic=False, recs_for=None):
        """One job per book that has something convertible. The source format is left in place.

        ``recs_for(book_id, src_fmt)`` supplies per-book recommendations and takes precedence over
        ``recs``; the bulk window needs it because "use saved conversion settings for individual
        books" means the options genuinely differ from one book to the next.
        """
        db = self._db()
        jobs = []
        settings = current_settings()
        polish_on, polish_ops = polish_settings(automatic=automatic)
        beautify = beautify_enabled()
        dry_run = self.dry_run if self.dry_run is not None else dry_run_enabled()
        recs = list(recs)
        for book_id in book_ids:
            fmts = {f.upper() for f in (db.formats(book_id) or ())}
            src_fmt = pick_source(fmts, preferred)
            if not src_fmt:
                continue
            src = PersistentTemporaryFile('.' + src_fmt.lower())
            src.close()
            db.copy_format_to(book_id, src_fmt, src.name)
            out = PersistentTemporaryFile('.epub')
            out.close()
            book_recs = recs_for(book_id, src_fmt) if recs_for is not None else recs
            jobs.append({
                'book_id': book_id,
                'title': db.field_for('title', book_id),
                'path': out.name,
                'convert_from': src.name,
                # the metadata recommendation goes last so it wins over the dialog's
                'recommendations': list(book_recs) + self._metadata_recommendations(book_id),
                'settings': settings,
                'polish_ops': polish_ops if polish_on else None,
                'target_version': target_epub_version(),
                'beautify': beautify,
                'dry_run': dry_run,
            })
        return jobs

    def _epub_jobs(self, book_ids, automatic=False):
        """One job per selected book that actually has an EPUB."""
        db = self._db()
        jobs, missing = [], []
        settings = current_settings()
        polish_on, polish_ops = polish_settings(automatic=automatic)
        beautify = beautify_enabled()
        dry_run = self.dry_run if self.dry_run is not None else dry_run_enabled()
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
                'beautify': beautify,
                'dry_run': dry_run,
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
        from calibre_plugins.epub_layout_fix.automation import ImportWatcher

        self.watcher = ImportWatcher(self)
        self.qaction.triggered.connect(self.run)
        m = self.qaction.menu() or QMenu(self.gui)
        self.qaction.setMenu(m)
        self.create_menu_action(m, 'eplf_fix_dialog', _('Fix layout...'),
                                icon=ICON, triggered=self.run)
        self.create_menu_action(m, 'eplf_fix_quick', _('Fix layout (last settings)'),
                                icon=ICON, triggered=self.run_quick)
        self.create_menu_action(m, 'eplf_convert', _('Convert to EPUB and fix...'),
                                icon=ICON, triggered=self.run_convert)
        self.create_menu_action(m, 'eplf_fix_dry', _('Fix layout (dry run)'),
                                icon=ICON, triggered=self.run_dry)
        m.addSeparator()
        self.create_menu_action(m, 'eplf_settings', _('Settings...'),
                                icon='config.png', triggered=self.show_settings)

    # -- automatic runs -----------------------------------------------------------------
    def initialization_complete(self):
        self.watcher.attach(self.gui.current_db)

    def library_changed(self, db):
        self.watcher.attach(db)

    def shutting_down(self):
        self.watcher.detach()

    def run_automatic(self, book_ids):
        """Called by the watcher once an import has settled. Never shows a dialog up front."""
        db = self._db()
        to_fix, to_convert = [], []
        for book_id in book_ids:
            fmts = {f.upper() for f in (db.formats(book_id) or ())}
            (to_fix if 'EPUB' in fmts else to_convert).append(book_id)

        jobs = self._epub_jobs(to_fix, automatic=True)[0] if to_fix else []
        if to_convert and auto_settings()['convert_formats']:
            jobs += self._convert_jobs(to_convert, automatic=True)
        if not jobs:
            return
        self._start(jobs, _('Fixing %d newly added book(s)') % len(jobs), silent=True)

    def show_settings(self):
        self.interface_action_base_plugin.do_user_config(self.gui)

    def run_quick(self):
        ids = self._require_selection()
        if not ids:
            return
        jobs, _missing = self._epub_jobs(ids)
        self._start(jobs, _('Fixing layout of %d book(s)') % len(jobs))

    def run_dry(self):
        """One dry run, whatever the stored setting says, and without changing it."""
        ids = self._require_selection()
        if not ids:
            return
        previous, self.dry_run = self.dry_run, True
        try:
            jobs, _missing = self._epub_jobs(ids)
        finally:
            self.dry_run = previous
        if not jobs:
            return error_dialog(self.gui, _('No EPUB'),
                                _('None of the selected books has an EPUB format.'), show=True)
        self._start(jobs, _('Dry run over %d book(s)') % len(jobs))

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
        bulk = len(ids) > 1

        # One book gets calibre's Convert window, several get its Bulk convert window - the same
        # choice calibre makes. The bulk window deliberately drops the per-book categories
        # (Metadata, Debug, input format), which the single window would otherwise have applied
        # to books they were never filled in for.
        try:
            from calibre_plugins.epub_layout_fix.dialog import (make_bulk_convert_dialog,
                                                                make_convert_dialog)
            if bulk:
                d = make_bulk_convert_dialog(self.gui, self.gui.current_db, ids)
            else:
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

        if bulk:
            jobs = self._convert_jobs(
                ids, recs_for=bulk_recommendations(self.gui.current_db, d))
        else:
            recs = list(getattr(d, 'recommendations', []) or [])
            jobs = self._convert_jobs(ids, recs, getattr(d, 'input_format', None))
        if not jobs:
            return error_dialog(self.gui, _('Nothing to convert'),
                                _('None of the selected books has a convertible format.'),
                                show=True)
        self._start(jobs, _('Converting and fixing %d book(s)') % len(jobs))

    _pick_source = staticmethod(pick_source)


class ReportGui(_BaseGui):
    """Say what a run would change, and change nothing.

    Useful before turning the plugin loose on a library: it answers "which of these books
    actually need work, and why was everything else left alone?"
    """

    name = 'EPUB Layout Fix - report'
    action_spec = (_('Report layout problems...'), ICON,
                   _('List what would be changed in the selected books, without touching them'),
                   None)

    def run(self):
        ids = self._require_selection()
        if not ids:
            return
        jobs, missing = self._epub_jobs(ids)
        if missing and not jobs:
            return error_dialog(self.gui, _('No EPUB'),
                                _('None of the selected books has an EPUB format.'), show=True)
        self._start(jobs, _('Examining %d book(s)') % len(jobs),
                    worker=run_report_worker(), kind='report')

    def _report(self, results, silent=False, kind='fix'):
        if kind != 'report':
            return _BaseGui._report(self, results, silent=silent, kind=kind)
        need = [r['book_id'] for r in results
                if r.get('book_id') is not None and r.get('changed') and not r.get('error')]
        self._flag_books(need)

        from calibre_plugins.epub_layout_fix.report_dialog import ReportDialog
        ReportDialog(self.gui, results, marked=self.MARK_LABEL if need else None).exec()
        self._offer_filter(len(need))

    def _commit_results(self, results):
        """A report writes nothing back; only the temporary copies need clearing up."""
        for r in results:
            try:
                os.remove(r['path'])
            except OSError:
                pass


def run_report_worker():
    from calibre_plugins.epub_layout_fix.jobs import run_report
    return run_report
