#!/usr/bin/env python3
"""Build (and optionally install) the calibre plugin zip.

    python build.py            # -> dist/EPUB-Layout-Fix.zip
    python build.py --install  # also runs calibre-customize -a on it

The zip is flat: calibre expects __init__.py, the plugin-import-name marker and any icons at the
archive root, not inside a folder.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'calibre_plugins', 'epub_layout_fix')
DIST = os.path.join(ROOT, 'dist')
NAME = 'EPUB-Layout-Fix.zip'

INCLUDE_SUFFIXES = ('.py', '.txt', '.png', '.svg', '.json')
EXCLUDE_DIRS = {'__pycache__'}


def collect():
    for entry in sorted(os.listdir(SRC)):
        full = os.path.join(SRC, entry)
        if os.path.isdir(full):
            if entry in EXCLUDE_DIRS:
                continue
            for sub in sorted(os.listdir(full)):
                if sub.endswith(INCLUDE_SUFFIXES):
                    yield os.path.join(full, sub), '%s/%s' % (entry, sub)
        elif entry.endswith(INCLUDE_SUFFIXES):
            yield full, entry


def build():
    if not os.path.isdir(SRC):
        raise SystemExit('plugin source not found: %s' % SRC)
    marker = 'plugin-import-name-epub_layout_fix.txt'
    if not os.path.exists(os.path.join(SRC, marker)):
        raise SystemExit('missing %s - calibre cannot import a multi-file plugin without it'
                         % marker)

    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, NAME)
    if os.path.exists(out):
        os.remove(out)

    files = list(collect())
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for full, arc in files:
            z.write(full, arc)

    print('built %s (%d files, %.1f KB)' % (out, len(files), os.path.getsize(out) / 1024.0))
    return out


def calibre_is_running():
    """Installing while the GUI is open is silently undone: calibre writes its stale in-memory
    plugin list back on exit, removing the freshly installed zip."""
    try:
        if sys.platform == 'win32':
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq calibre.exe'],
                                 capture_output=True, text=True).stdout
            return 'calibre.exe' in out
        out = subprocess.run(['pgrep', '-x', 'calibre'], capture_output=True, text=True)
        return out.returncode == 0
    except Exception:                                          # noqa: BLE001
        return False


def install(path):
    if calibre_is_running():
        print('REFUSING to install: calibre is running.\n'
              '  Installing now would be undone when calibre exits - it writes its stale\n'
              '  in-memory plugin list back over the change.\n'
              '  Close calibre and re-run, or load the zip from Preferences -> Plugins.')
        return 1
    exe = shutil.which('calibre-customize')
    for cand in (exe, r'C:\Program Files\Calibre2\calibre-customize.exe'):
        if cand and os.path.exists(cand):
            print('installing with', cand)
            r = subprocess.run([cand, '-a', path], capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())
            return r.returncode
    print('calibre-customize not found; load the zip manually via '
          'Preferences -> Plugins -> Load plugin from file')
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('-i', '--install', action='store_true', help='install after building')
    args = ap.parse_args()
    # Build first, always. An earlier version bailed out before building when calibre was
    # running, which left a stale zip on disk that looked freshly built.
    path = build()
    if args.install:
        return install(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
