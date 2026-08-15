#!/usr/bin/env python3
"""Attach a file to the running job as an artifact, without an action.

    python scripts/upload_artifact.py EPUB-Layout-Fix dist/EPUB-Layout-Fix.zip

upload-artifact@v4 refuses to run anywhere it does not recognise as github.com, its fork lives
only on Forgejo, and v3 is deprecated - GitHub fails a job for merely naming either. A run step
is the one form that does not have to resolve on both forges.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (os.environ.get('ACTIONS_RUNTIME_URL') or '').rstrip('/')
TOKEN = os.environ.get('ACTIONS_RUNTIME_TOKEN', '')
RUN = os.environ.get('GITHUB_RUN_ID', '')
VERSION = 'api-version=6.0-preview'


def call(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + TOKEN)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req) as res:
            body = res.read()
            return json.loads(body) if body.startswith(b'{') else None
    except urllib.error.HTTPError as e:
        print('%s %s -> %s' % (method, url.split('?')[0], e.code))
        print(e.read().decode('utf-8', 'replace')[:500])
        raise SystemExit(1)


def main():
    if len(sys.argv) != 3:
        print('usage: upload_artifact.py <name> <file>')
        return 2
    name, path = sys.argv[1], sys.argv[2]

    for key in ('ACTIONS_RUNTIME_URL', 'ACTIONS_RUNTIME_TOKEN', 'GITHUB_RUN_ID'):
        if not os.environ.get(key):
            print('%s is not set; this only runs inside a job' % key)
            return 1

    with open(path, 'rb') as f:
        payload = f.read()

    workflow = '%s/_apis/pipelines/workflows/%s/artifacts?%s' % (BASE, RUN, VERSION)
    container = call('POST', workflow,
                     json.dumps({'Type': 'actions_storage', 'Name': name}).encode('utf-8'),
                     {'Content-Type': 'application/json'})

    item = urllib.parse.quote('%s/%s' % (name, os.path.basename(path)))
    call('PUT', '%s?itemPath=%s' % (container['fileContainerResourceUrl'], item), payload, {
        'Content-Type': 'application/octet-stream',
        'Content-Range': 'bytes 0-%d/%d' % (len(payload) - 1, len(payload)),
        'x-tfs-filelength': str(len(payload)),
        'x-actions-results-md5': base64.b64encode(hashlib.md5(payload).digest()).decode('ascii'),
    })

    call('PATCH', '%s&artifactName=%s' % (workflow, urllib.parse.quote(name)),
         json.dumps({'Size': len(payload)}).encode('utf-8'),
         {'Content-Type': 'application/json'})

    print('uploaded %s as %s (%.1f KB)' % (path, name, len(payload) / 1024.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
