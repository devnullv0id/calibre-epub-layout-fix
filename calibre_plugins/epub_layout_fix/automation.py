#!/usr/bin/env python3
"""Run the repairs by themselves whenever books arrive in the library.

Hooks calibre's database event stream rather than the Add books action, so every route into the
library is covered: Add books, drag and drop, the content server, a connected device and a
watched folder all end up creating book records through the same API.

The hard part is not the trigger, it is not re-triggering on our own writes: committing a repaired
book calls ``add_format``, which raises exactly the event that started the run. Three independent
guards are in place - see :class:`ImportWatcher`.
"""

from __future__ import annotations

try:
    from qt.core import QTimer
except ImportError:                                            # older calibre
    from PyQt5.Qt import QTimer

from calibre.gui2 import FunctionDispatcher, question_dialog

from calibre_plugins.epub_layout_fix.config import auto_settings

__license__ = 'GPL v3'

#: How long a book id stays in the suppression set after we write to it. Database events are
#: delivered on their own thread, so they can still arrive after the write call returns.
SUPPRESS_MS = 15000


class ImportWatcher(object):
    """Watches one library for newly added books and hands them to the owning action.

    Guards against re-entering on our own output, in increasing order of stubbornness:

    1. ``suppress()`` - book ids we are about to write to are ignored until a timer clears them;
    2. an existing ``ORIGINAL_EPUB`` format means the book has been processed before;
    3. ``_seen`` - a book is never queued twice in one session, whatever the events say.
    """

    def __init__(self, action):
        self.action = action
        self.gui = action.gui
        self.db = None

        self._pending = set()
        self._suppressed = set()
        self._seen = set()
        self._asked_about = 0

        # add_listener keeps only a weak reference, so the bound method has to be held here or it
        # is collected the moment attach() returns and no event is ever delivered.
        self._listener = self._on_db_event
        # events arrive on calibre's event-dispatcher thread; everything after this runs on the
        # GUI thread, which is the only thread allowed to touch the model or start a job
        self._to_gui = FunctionDispatcher(self._book_touched)

        self._timer = QTimer(self.gui)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    # -- wiring -------------------------------------------------------------------------
    def attach(self, db):
        """Listen to ``db``. Safe to call repeatedly; the library can change under us."""
        if db is None:
            return
        new_api = getattr(db, 'new_api', db)
        if new_api is self.db:
            return
        self.detach()
        try:
            new_api.add_listener(self._listener)
        except Exception:                                      # noqa: BLE001 - never break startup
            import traceback
            traceback.print_exc()
            return
        self.db = new_api
        # a different library has its own book ids, so nothing carried over is meaningful
        self._pending.clear()
        self._seen.clear()
        self._suppressed.clear()

    def detach(self):
        if self.db is None:
            return
        try:
            self.db.remove_listener(self._listener)
        except Exception:                                      # noqa: BLE001
            pass
        self.db = None
        try:
            self._timer.stop()
        except Exception:                                      # noqa: BLE001 - already destroyed
            pass

    def suppress(self, book_ids):
        """Ignore events for these books for a while - we are the ones causing them."""
        ids = set(book_ids)
        if not ids:
            return
        self._suppressed |= ids
        QTimer.singleShot(SUPPRESS_MS, lambda: self._suppressed.difference_update(ids))

    # -- the event stream ---------------------------------------------------------------
    def _on_db_event(self, event_type, library_id, event_data):
        """Called on calibre's event-dispatcher thread. Does as little as possible."""
        try:
            if not auto_settings()['enabled']:
                return
            name = getattr(event_type, 'name', str(event_type))
            if name == 'book_created':
                book_id = event_data[0]
            elif name == 'format_added':
                book_id, fmt = event_data[0], event_data[1]
                if (fmt or '').upper() != 'EPUB':
                    return
            else:
                return
            if book_id in self._suppressed or book_id in self._seen:
                return
            self._to_gui(book_id)
        except Exception:                                      # noqa: BLE001 - a listener that
            pass                                               # raises is dropped by calibre

    def _book_touched(self, book_id):
        """GUI thread. Collect, and restart the settle timer."""
        if book_id in self._suppressed or book_id in self._seen:
            return
        self._pending.add(book_id)
        self._timer.start(auto_settings()['debounce'] * 1000)

    # -- the run ------------------------------------------------------------------------
    def _flush(self):
        book_ids, self._pending = sorted(self._pending), set()
        if not book_ids:
            return
        opts = auto_settings()
        if not opts['enabled']:
            return

        db = self.db
        if db is None:
            return

        wanted = []
        for book_id in book_ids:
            if book_id in self._seen or book_id in self._suppressed:
                continue
            try:
                fmts = {f.upper() for f in (db.formats(book_id) or ())}
            except Exception:                                  # noqa: BLE001 - deleted meanwhile
                continue
            if not fmts:
                # the record exists but no file has landed yet; the format_added event will
                # bring us back here
                continue
            if 'ORIGINAL_EPUB' in fmts:
                self._seen.add(book_id)                        # already repaired once
                continue
            if 'EPUB' not in fmts and not opts['convert_formats']:
                continue
            wanted.append(book_id)

        if not wanted:
            return

        if len(wanted) > opts['max_books']:
            if not question_dialog(
                    self.gui, _('Fix %d books?') % len(wanted),
                    _('%(n)d books were just added and "%(what)s" is on. That queues one job per '
                      'book. Process them now?')
                    % {'n': len(wanted), 'what': _('Fix books automatically when they are added '
                                                   'to the library')}):
                self._seen.update(wanted)
                return

        self._seen.update(wanted)
        self.action.run_automatic(wanted)
