#!/usr/bin/env python3
"""Build (and optionally install) the calibre plugin zip.

    python build.py            # -> dist/EPUB-Layout-Fix.zip
    python build.py --install  # also runs calibre-customize -a on it
    python build.py --restart  # close calibre, build, install, start calibre again

The zip is flat: calibre expects __init__.py, the plugin-import-name marker and any icons at the
archive root, not inside a folder.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'calibre_plugins', 'epub_layout_fix')
DIST = os.path.join(ROOT, 'dist')
NAME = 'EPUB-Layout-Fix.zip'

INCLUDE_SUFFIXES = ('.py', '.txt', '.png', '.svg', '.json')
EXCLUDE_DIRS = {'__pycache__'}


def plugin_version():
    """PLUGIN_VERSION from action_base.py, as "x.y.z".

    Parsed rather than imported: action_base imports calibre, which is not available to the
    plain interpreter this script is meant to run under.
    """
    import ast

    path = os.path.join(SRC, 'action_base.py')
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read(), path)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if getattr(node.targets[0], 'id', None) != 'PLUGIN_VERSION':
            continue
        return '.'.join(str(e.value) for e in node.value.elts)
    raise SystemExit('PLUGIN_VERSION not found in %s' % path)


def check_version(expected):
    """Guard for the release job: a tag must not ship a zip claiming a different version."""
    expected = expected.lstrip('v')
    actual = plugin_version()
    if actual != expected:
        print('version mismatch: tag says %s, PLUGIN_VERSION is %s' % (expected, actual))
        return 1
    print('version %s matches' % actual)
    return 0


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


def calibre_exe(name='calibre'):
    """The full path to one of calibre's executables, or None."""
    found = shutil.which(name)
    if found:
        return found
    for folder in (r'C:\Program Files\Calibre2', r'C:\Program Files (x86)\Calibre2',
                   '/Applications/calibre.app/Contents/MacOS', '/usr/bin', '/usr/local/bin'):
        cand = os.path.join(folder, name + ('.exe' if sys.platform == 'win32' else ''))
        if os.path.exists(cand):
            return cand
    return None


def stop_calibre(timeout=30):
    """Ask calibre to quit, then insist. -> True once nothing is running.

    calibre writes its plugin list out on exit, so it has to be *gone* before the install, not
    merely asked to leave.
    """
    if not calibre_is_running():
        return True

    print('closing calibre...')
    if sys.platform == 'win32':
        # /IM without /F sends WM_CLOSE, which lets calibre save its state properly
        subprocess.run(['taskkill', '/IM', 'calibre.exe'], capture_output=True, text=True)
    else:
        subprocess.run(['pkill', '-x', 'calibre'], capture_output=True, text=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not calibre_is_running():
            # the GUI process is gone; give it a moment to finish flushing its config
            time.sleep(1.0)
            return True
        time.sleep(0.5)

    print('  still running after %ds, forcing' % timeout)
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/F', '/IM', 'calibre.exe'], capture_output=True, text=True)
    else:
        subprocess.run(['pkill', '-9', '-x', 'calibre'], capture_output=True, text=True)
    time.sleep(2.0)
    return not calibre_is_running()


def start_calibre():
    exe = calibre_exe('calibre')
    if not exe:
        print('calibre executable not found; start it yourself')
        return 1
    print('starting', exe)
    kwargs = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
    else:
        kwargs['start_new_session'] = True
    subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    return 0


def install(path):
    if calibre_is_running():
        print('REFUSING to install: calibre is running.\n'
              '  Installing now would be undone when calibre exits - it writes its stale\n'
              '  in-memory plugin list back over the change.\n'
              '  Close calibre and re-run, or load the zip from Preferences -> Plugins.')
        return 1
    exe = calibre_exe('calibre-customize')
    if not exe:
        print('calibre-customize not found; load the zip manually via '
              'Preferences -> Plugins -> Load plugin from file')
        return 1
    print('installing with', exe)
    r = subprocess.run([exe, '-a', path], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('-i', '--install', action='store_true', help='install after building')
    ap.add_argument('-r', '--restart', action='store_true',
                    help='close calibre, build, install, then start calibre again')
    ap.add_argument('--check-version', metavar='TAG',
                    help='fail unless PLUGIN_VERSION matches TAG, then exit')
    args = ap.parse_args()

    if args.check_version:
        return check_version(args.check_version)

    if args.restart and not stop_calibre():
        print('could not close calibre; aborting rather than installing into a running instance')
        return 1

    # Build first, always. An earlier version bailed out before building when calibre was
    # running, which left a stale zip on disk that looked freshly built.
    path = build()

    if args.install or args.restart:
        rc = install(path)
        if args.restart:
            # Start calibre back up even if the install failed, so the user is never left
            # without their library because of a build error.
            start_calibre()
        return rc
    return 0


if __name__ == '__main__':
    sys.exit(main())
