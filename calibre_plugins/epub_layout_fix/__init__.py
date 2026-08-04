#!/usr/bin/env python3
"""EPUB Layout Fix - a calibre plugin.

Repairs full-page images that readers clip or push off the page, and covers that calibre
generates with ``preserveAspectRatio="none"``.

calibre loads exactly one Plugin subclass per zip, so only the primary action is exported here.
It registers its two companions at load time (see
:meth:`action_base.FixLayoutAction.initialize`), which is how one archive ends up contributing
three independently placeable toolbar buttons.
"""

from __future__ import annotations

from calibre_plugins.epub_layout_fix.action_base import (  # noqa: F401 - found by calibre
    FixLayoutAction,
)

__license__ = 'GPL v3'
__copyright__ = '2026, devnullv0id'
