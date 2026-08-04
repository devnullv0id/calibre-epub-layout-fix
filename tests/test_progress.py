#!/usr/bin/env python3
"""The job status must follow the actual stage, not sit on "Starting".

    calibre-debug tests/test_progress.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []


def check(name, cond, msg):
    print(('  PASS  ' if cond else '  FAIL  ') + '%-14s %s' % (name, msg))
    if not cond:
        FAILURES.append('%s: %s' % (name, msg))


class FakeQueue(object):
    """Stands in for the notifications queue a ThreadedJob hands to its worker."""

    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def main():
    from calibre.customize.ui import initialize_plugins
    initialize_plugins()
    from calibre_plugins.epub_layout_fix import jobs
    from calibre_plugins.epub_layout_fix.config import current_settings

    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    tmp = tempfile.mkdtemp(prefix='eplfprog')
    try:
        book = os.path.join(tmp, 'b.epub')
        shutil.copy(os.path.join(HERE, 'fixtures', 'epub2.epub'), book)
        shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)

        q = FakeQueue()
        res = jobs.run_single({'path': book, 'title': 'Test Book',
                               'settings': current_settings(),
                               'polish_ops': {'upgrade_book': True},
                               'target_version': '3'},
                              notifications=q)

        check('run', not res['error'], 'completed: %s' % (res['error'] or 'ok'))

        msgs = [m for _f, m in q.items]
        fracs = [f for f, _m in q.items]
        print('        stages reported: %s' % msgs)

        check('progress', len(q.items) >= 4,
              '%d progress updates (not just "Starting")' % len(q.items))
        for want in ('Polishing', 'Upgrading to EPUB 3', 'Repairing images and cover', 'Done'):
            check('progress', any(want in m for m in msgs), 'reports %r' % want)
        check('progress', fracs == sorted(fracs), 'fractions increase monotonically: %s' % fracs)
        check('progress', fracs[-1] == 1.0, 'ends at 100%% (got %s)' % fracs[-1])

        # a conversion job should additionally report the convert stage
        q2 = FakeQueue()
        src = os.path.join(tmp, 'src.epub')
        shutil.copy(book, src)
        out = os.path.join(tmp, 'out.epub')
        res2 = jobs.run_single({'path': out, 'title': 'Converted', 'convert_from': src,
                                'settings': current_settings(), 'polish_ops': None,
                                'target_version': '3'}, notifications=q2)
        msgs2 = [m for _f, m in q2.items]
        check('convert', not res2['error'], 'conversion job ok: %s' % (res2['error'] or 'yes'))
        check('convert', any('Converting' in m for m in msgs2),
              'reports the conversion stage: %s' % msgs2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%d failure(s)' % len(FAILURES))
    for f in FAILURES:
        print('   !!', f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
