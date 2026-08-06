#!/usr/bin/env python3
"""Judge the fix by calibre's opinion rather than the plugin's own.

    calibre-debug tests/test_oracle.py
    EPLF_BOOKS=/path/to/books calibre-debug tests/test_oracle.py     # ... and real books

Runs ``calibre.ebooks.oeb.polish.check.main.run_checks`` - the editor's *Tools -> Check book* -
over each book before and after the fix, and fails if a warning or error class appears more often
afterwards. Every other suite here checks the plugin against assumptions the plugin itself makes;
this one checks it against a validator that has never heard of it.

Real books are opt-in because calibre's checker is slow: a 32-book library takes several minutes.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAILURES = []


def check(name, cond, msg):
    print(('  PASS  ' if cond else '  FAIL  ') + '%-12s %s' % (name, msg))
    if not cond:
        FAILURES.append('%s: %s' % (name, msg))


def note(msg):
    print('        %s' % msg)


#: Checks a passing fix is still expected to raise.
#:
#: ``UnreferencedResource`` fires because a rewritten page deliberately does not link the book's
#: stylesheets - that is what stops anything in the book overriding the result. In a one-page
#: fixture that page was the stylesheet's only referrer, so it becomes unreferenced; in a real
#: book the other pages still link it, which is why 11 of 17 real books came out of this checker
#: *cleaner* than they went in. An all-image book would leave the stylesheet as unused weight,
#: which is untidy and not a defect.
EXPECTED = {'UnreferencedResource'}


def problems(path):
    """-> ``{(level, check class): count}`` from calibre's own checker.

    Grouped by class rather than by message: the message carries line numbers, which move when a
    page is rewritten, and a moved line is not a new problem.
    """
    from calibre.ebooks.oeb.polish.check.main import run_checks
    from calibre.ebooks.oeb.polish.container import get_container
    from calibre.utils.logging import Log

    counts = {}
    for err in run_checks(get_container(path, Log(), tweak_mode=True)):
        key = (getattr(err, 'level', 0), err.__class__.__name__)
        counts[key] = counts.get(key, 0) + 1
    return counts


def books():
    """The fixtures, plus anything in EPLF_BOOKS."""
    subprocess.run([sys.executable, os.path.join(HERE, 'make_fixtures.py')],
                   check=True, capture_output=True, cwd=HERE)
    out = [os.path.join(HERE, 'fixtures', f)
           for f in sorted(os.listdir(os.path.join(HERE, 'fixtures')))
           if f.lower().endswith('.epub')]
    extra = os.environ.get('EPLF_BOOKS', '')
    if extra and os.path.isdir(extra):
        out += [os.path.join(extra, f) for f in sorted(os.listdir(extra))
                if f.lower().endswith('.epub')]
    return out


def main():
    from calibre.customize.ui import initialize_plugins
    initialize_plugins()
    sys.path.insert(0, os.path.join(ROOT, 'calibre_plugins', 'epub_layout_fix'))
    import fixer

    WARN = 2                                   # calibre: INFO 1, WARN 2, ERROR 3
    tmp = tempfile.mkdtemp(prefix='eplforacle')
    examined = improved = 0
    try:
        for book in books():
            name = os.path.basename(book)
            work = os.path.join(tmp, 'w.epub')
            shutil.copy(book, work)

            try:
                before = problems(work)
            except Exception as e:                             # noqa: BLE001 - checker is picky
                note('%-16s calibre could not open it: %s' % (name, e))
                continue

            res = fixer.fix_epub(work)
            if res.error:
                note('%-16s engine declined: %s' % (name, res.error[:50]))
                continue
            if not res.changed:
                continue

            try:
                after = problems(work)
            except Exception:                                  # noqa: BLE001
                check('oracle', False, '%s: calibre cannot open the book after the fix' % name)
                continue

            examined += 1
            worse = {'%s (level %d)' % (cls, lvl): (before.get((lvl, cls), 0), n)
                     for (lvl, cls), n in after.items()
                     if lvl >= WARN and n > before.get((lvl, cls), 0)
                     and cls not in EXPECTED}
            check('oracle', not worse,
                  '%s: calibre Check book finds nothing new%s'
                  % (name, '' if not worse else ' (%s)' % worse))

            fewer = sum(max(0, n - after.get(k, 0))
                        for k, n in before.items() if k[0] >= WARN)
            if fewer:
                improved += 1
                note('%-16s %d fewer warning(s)/error(s) than before' % (name, fewer))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(os.path.join(HERE, 'fixtures'), ignore_errors=True)

    print('\n%d book(s) put through calibre Check book, %d came out cleaner'
          % (examined, improved))
    print('\n%d failure(s)' % len(FAILURES))
    for f in FAILURES:
        print('   !!', f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
