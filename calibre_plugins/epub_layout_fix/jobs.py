#!/usr/bin/env python3
"""Worker entry points.

Everything here runs on calibre's job thread. It must not touch the GUI or the library database
- book data arrives as file paths and results go back through the return value, with the caller
writing to the library on the GUI thread.
"""

from __future__ import annotations

import os
import traceback

from calibre_plugins.epub_layout_fix import fixer

__license__ = 'GPL v3'


def upgrade_to_epub3(path, log=None):
    """Run calibre's own "Upgrade book internals" on a file in place.

    Returns ``(ok, message)``. Already-EPUB 3 books are left alone.
    """
    if fixer.is_epub3(path):
        return True, 'already EPUB 3'
    return run_polish(path, {'upgrade_book': True}, log=log)


def run_polish(path, operations, log=None, cover_path=None, opf_path=None):
    """Run calibre's polish on a file in place.

    ``operations`` is the ``{option: bool}`` map from the Polish panel. Options calibre expects
    as file paths (cover, opf) are supplied separately when the caller has them.
    """
    enabled = {k: v for k, v in (operations or {}).items() if v}
    if not enabled and not cover_path and not opf_path:
        return True, 'nothing to do'

    try:
        from calibre.ebooks.oeb.polish.main import ALL_OPTS, polish
        from calibre.utils.logging import Log
    except ImportError as e:
        return False, 'polish unavailable: %s' % e

    class _Opts(object):
        pass

    opts = _Opts()
    for key, default in ALL_OPTS.items():
        setattr(opts, key, enabled.get(key, default))
    if cover_path:
        opts.cover = cover_path
    if opf_path:
        opts.opf = opf_path

    messages = []

    def report(msg):
        messages.append(str(msg))

    try:
        polish({path: path}, opts, log or Log(), report)
    except Exception as e:                                     # noqa: BLE001
        return False, '%s: %s' % (type(e).__name__, e)
    return True, '; '.join(messages[-3:])


def convert_to_epub(src_path, dest_path, recommendations=None, log=None, target_version='3'):
    """Convert any calibre-readable format to EPUB, in process.

    Two options are forced regardless of what the conversion window says:

    * ``preserve_cover_aspect_ratio`` - without it calibre regenerates the cover with
      preserveAspectRatio="none", which is one of the defects this plugin exists to repair.
    * ``epub_version`` - so the output starts at the requested version instead of being
      produced as EPUB 2 and upgraded afterwards.
    """
    try:
        from calibre.customize.conversion import OptionRecommendation
        from calibre.ebooks.conversion.plumber import Plumber
        from calibre.utils.logging import Log
    except ImportError as e:
        return False, 'conversion unavailable: %s' % e

    try:
        plumber = Plumber(src_path, dest_path, log or Log())
        recs = list(recommendations or [])
        recs.append(('preserve_cover_aspect_ratio', True, OptionRecommendation.HIGH))
        if target_version in ('2', '3'):
            recs.append(('epub_version', target_version, OptionRecommendation.HIGH))
        plumber.merge_ui_recommendations(recs)
        plumber.run()
    except Exception as e:                                     # noqa: BLE001
        return False, '%s: %s' % (type(e).__name__, e)
    return True, ''


def process_book(path, settings, polish_ops=None, target_version='3',
                 convert_from=None, recommendations=None, log=None, progress=None):
    """The whole pipeline for one file: convert -> polish -> upgrade -> fix.

    ``progress(fraction, message)`` is called as each stage starts, so the job list shows what
    is actually happening rather than a single "Starting ..." for the whole run.

    Returns a plain dict so it crosses the job boundary cleanly.
    """
    def step(frac, msg):
        if progress is not None:
            progress(frac, msg)

    steps = []
    try:
        if convert_from:
            step(0.05, _('Converting to EPUB'))
            ok, msg = convert_to_epub(convert_from, path, recommendations, log, target_version)
            steps.append(('convert', ok, msg))
            if not ok:
                return _as_dict(path, steps, None, 'conversion failed: %s' % msg)

        if polish_ops:
            step(0.55, _('Polishing'))
            ok, msg = run_polish(path, polish_ops, log)
            steps.append(('polish', ok, msg))
            # a failed polish is not fatal; the layout fixes are still worth applying

        if target_version == '3':
            step(0.75, _('Upgrading to EPUB 3'))
            ok, msg = upgrade_to_epub3(path, log)
            steps.append(('upgrade', ok, msg))

        step(0.85, _('Repairing images and cover'))
        result = fixer.fix_epub(path, settings)
        steps.append(('fix', not result.error and not result.problems,
                      result.error or '; '.join(result.problems)))
        step(1.0, _('Done'))
        return _as_dict(path, steps, result, result.error)
    except Exception:                                          # noqa: BLE001
        return _as_dict(path, steps, None, traceback.format_exc())


def _as_dict(path, steps, result, error):
    d = {
        'path': path,
        'name': os.path.basename(path),
        'steps': steps,
        'error': error,
        'image_pages': 0,
        'svg_repaired': 0,
        'cover_fixed': False,
        'skipped': 0,
        'changed': False,
        'details': [],
        'ledger': [],
        'problems': [],
    }
    if result is not None:
        d.update({
            'image_pages': result.image_pages,
            'svg_repaired': result.svg_repaired,
            'cover_fixed': result.cover_fixed,
            'skipped': result.skipped,
            'changed': result.changed,
            'details': list(result.details),
            'ledger': list(result.ledger),
            'problems': list(result.problems),
        })
    return d


def _notify(notifications, frac, msg):
    if notifications is not None:
        try:
            notifications.put((frac, msg))
        except Exception:                                      # noqa: BLE001
            pass


def run_single(job, notifications=None, abort=None, log=None):
    """Process one book. Entry point for a per-book ThreadedJob.

    One job per book mirrors calibre's own Convert action: each book gets its own progress and
    can be cancelled individually, instead of a single opaque batch job.
    """
    title = job.get('title') or os.path.basename(job['path'])

    _notify(notifications, 0.01, _('Starting'))
    if abort is not None and abort.is_set():
        return _as_dict(job['path'], [], None, 'cancelled')

    def progress(frac, msg):
        _notify(notifications, frac, msg)

    res = process_book(
        job['path'], job['settings'], job.get('polish_ops'),
        job.get('target_version', '3'), job.get('convert_from'),
        job.get('recommendations'), log, progress=progress)

    res['book_id'] = job.get('book_id')
    res['title'] = title
    return res
