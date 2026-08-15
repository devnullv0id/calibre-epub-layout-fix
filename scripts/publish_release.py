#!/usr/bin/env python3
"""Publish TAG as a release with the plugin zip attached, on whichever forge this runs on.

    python scripts/publish_release.py dist/EPUB-Layout-Fix.zip

Reads GITHUB_API_URL, so the same call targets GitHub or Forgejo. Doing nothing when the
release already exists is deliberate: re-running must be harmless.
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = (os.environ.get('GITHUB_API_URL') or 'https://api.github.com').rstrip('/')
SERVER = (os.environ.get('GITHUB_SERVER_URL') or 'https://github.com').rstrip('/')
REPO = os.environ.get('GITHUB_REPOSITORY', '')
SHA = os.environ.get('GITHUB_SHA', '')
TOKEN = os.environ.get('RELEASE_TOKEN', '')
TAG = os.environ.get('TAG', '')

ON_GITHUB = 'api.github.com' in API


def request(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', ('Bearer ' if ON_GITHUB else 'token ') + TOKEN)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req) as res:
            body = res.read()
            return res.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')[:500]


def git(*args):
    try:
        return subprocess.run(('git',) + args, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return ''


def notes():
    tagged = git('rev-parse', '-q', '--verify', 'refs/tags/%s' % TAG)
    head = TAG if tagged else 'HEAD'
    previous = git('describe', '--tags', '--abbrev=0', head + '^' if tagged else head)
    if not previous:
        return ''
    parts = []
    log = git('log', '--no-merges', '--pretty=format:- %s', '%s..%s' % (previous, head))
    if log:
        parts.append('## What changed\n\n' + log)
    parts.append('**Full changelog**: %s/%s/compare/%s...%s' % (SERVER, REPO, previous, TAG))
    return '\n\n'.join(parts)


def multipart(field, filename, payload):
    boundary = '----publish-release-%s' % os.urandom(8).hex()
    kind = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    head = ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            'Content-Type: %s\r\n\r\n' % (boundary, field, filename, kind))
    body = head.encode('utf-8') + payload + ('\r\n--%s--\r\n' % boundary).encode('utf-8')
    return body, {'Content-Type': 'multipart/form-data; boundary=%s' % boundary}


def attach(release, path):
    name = os.path.basename(path)
    for asset in release.get('assets') or []:
        if asset.get('name') == name:
            print('%s is already attached' % name)
            return 0

    with open(path, 'rb') as f:
        payload = f.read()

    if ON_GITHUB:
        url = '%s?name=%s' % (release['upload_url'].split('{')[0], name)
        body = payload
        headers = {'Content-Type': mimetypes.guess_type(name)[0] or 'application/octet-stream'}
    else:
        url = '%s/repos/%s/releases/%s/assets?name=%s' % (API, REPO, release['id'], name)
        body, headers = multipart('attachment', name, payload)

    status, answer = request('POST', url, body, headers)
    if status not in (200, 201):
        print('attaching %s failed with %s' % (name, status))
        print(answer)
        return 1
    print('attached %s (%.1f KB)' % (name, len(payload) / 1024.0))
    return 0


def main():
    if len(sys.argv) != 2:
        print('usage: publish_release.py <file to attach>')
        return 2
    path = sys.argv[1]
    if not os.path.exists(path):
        print('nothing to attach at %s' % path)
        return 1

    for name in ('GITHUB_REPOSITORY', 'RELEASE_TOKEN', 'TAG'):
        if not os.environ.get(name):
            print('%s is not set' % name)
            return 1

    host = API.split('//', 1)[-1].split('/', 1)[0]

    status, existing = request('GET', '%s/repos/%s/releases/tags/%s' % (API, REPO, TAG))
    if status == 200:
        print('%s already exists on %s' % (TAG, host))
        return attach(existing, path)

    payload = json.dumps({
        'tag_name': TAG,
        'target_commitish': SHA,
        'name': TAG,
        'body': notes(),
        'draft': False,
        'prerelease': '-' in TAG,
    }).encode('utf-8')

    status, made = request('POST', '%s/repos/%s/releases' % (API, REPO), payload,
                           {'Content-Type': 'application/json'})
    if status not in (200, 201):
        print('%s refused to create %s: %s' % (host, TAG, status))
        print(made)
        return 1

    print('released %s on %s' % (TAG, host))
    return attach(made, path)


if __name__ == '__main__':
    sys.exit(main())
