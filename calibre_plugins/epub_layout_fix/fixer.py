#!/usr/bin/env python3
"""The layout-fixing engine.

Deliberately free of calibre and Qt imports so it can be exercised headlessly with a plain
Python interpreter. Everything the GUI needs is reachable through :func:`analyze_epub` and
:func:`fix_epub`.

Two defects are repaired:

1. Full-page images that the reader clips or pushes off the page. A page qualifies when its
   ``<body>`` holds a single image and no text, and the image resolves to at least
   ``min_width_percent`` of the column. Such pages are rewritten as a self-contained SVG page
   object so the SVG renderer fits the image in both dimensions instead of the CSS cascade.

2. Covers and other SVG pages carrying ``preserveAspectRatio="none"``, which stretches the image.
   These are repaired in place; the cover additionally gets a dark letterbox background.

Detection is structural and never keys off class names: the real CSS cascade is resolved (linked
stylesheets, ``<style>`` blocks and inline attributes, with specificity) to determine how wide an
image is actually displayed. This is what makes the engine work across books from any producer.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import struct
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

XHTML_NS = 'http://www.w3.org/1999/xhtml'
SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'
XH = '{%s}' % XHTML_NS
SVG = '{%s}' % SVG_NS
XLINK = '{%s}' % XLINK_NS

COVER_MARKER = 'epub-layout-fix:cover-bg'

#: Skip categories that may hide a genuine miss. Everything else is expected and stays quiet.
NOTABLE_SKIPS = frozenset({
    'captioned-candidate', 'multi-image', 'too-narrow', 'unreadable-image',
    'missing-image', 'no-src', 'svg-no-viewbox', 'unparsable',
})

TRACKED_PROPS = frozenset({
    'width', 'max-width', 'height', 'max-height',
    'margin-left', 'margin-right', 'padding-left', 'padding-right',
    'box-sizing', 'display',
})

DEFAULT_SETTINGS = {
    'fix_images': True,
    'min_width_percent': 80.0,
    'fix_covers': True,
    'dark_cover': True,
    'cover_color': '#000000',
    'preserve_anchors': True,
}


# --------------------------------------------------------------------------------------
# Image dimensions - parsed from file headers, no imaging library required
# --------------------------------------------------------------------------------------

def image_size(data):
    """Return ``(width, height)`` or ``None`` if the format is not understood."""
    if not data or len(data) < 24:
        return None

    # SVG is text; prefer viewBox because it is always in user units
    head = data[:2048].decode('utf-8', 'replace')
    if re.search(r'<svg\b', head, re.I | re.S):
        m = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)',
                      head, re.I | re.S)
        if m:
            w, h = int(round(float(m.group(1)))), int(round(float(m.group(2))))
            if w > 0 and h > 0:
                return w, h
        mw = re.search(r'\bwidth\s*=\s*["\']\s*([\d.]+)', head, re.I)
        mh = re.search(r'\bheight\s*=\s*["\']\s*([\d.]+)', head, re.I)
        if mw and mh:
            w, h = int(round(float(mw.group(1)))), int(round(float(mh.group(1))))
            if w > 0 and h > 0:
                return w, h
        return None

    if data[:8] == b'\x89PNG\r\n\x1a\n':
        w, h = struct.unpack('>II', data[16:24])
        return (w, h) if w and h else None

    if data[:3] == b'GIF':
        w, h = struct.unpack('<HH', data[6:10])
        return (w, h) if w and h else None

    # WebP - RIFF container with three sub-formats, each storing the size differently
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        fourcc = data[12:16]
        if fourcc == b'VP8 ' and len(data) >= 30:
            w = struct.unpack('<H', data[26:28])[0] & 0x3FFF
            h = struct.unpack('<H', data[28:30])[0] & 0x3FFF
            return (w, h) if w and h else None
        if fourcc == b'VP8L' and len(data) >= 25:
            b = struct.unpack('<I', data[21:25])[0]
            return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
        if fourcc == b'VP8X' and len(data) >= 30:
            w = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
            h = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
            return w, h
        return None

    # BMP - signed 32-bit little-endian; height may be negative for top-down bitmaps
    if data[:2] == b'BM' and len(data) >= 26:
        w, h = struct.unpack('<ii', data[18:26])
        h = abs(h)
        return (w, h) if w > 0 and h > 0 else None

    # JPEG - walk the segment chain to a start-of-frame marker
    if data[:2] == b'\xff\xd8':
        i = 2
        n = len(data)
        while i < n - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0xD9, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack('>HH', data[i + 5:i + 9])
                return (w, h) if w and h else None
            seg = struct.unpack('>H', data[i + 2:i + 4])[0]
            if seg < 2:
                return None
            i += 2 + seg
    return None


# --------------------------------------------------------------------------------------
# A small CSS cascade - just enough to answer "how wide is this image displayed?"
# --------------------------------------------------------------------------------------

_RULE_RE = re.compile(r'([^{}]+)\{([^{}]*)\}')
_COMMENT_RE = re.compile(r'/\*.*?\*/', re.S)
_ATRULE_RE = re.compile(r'@(?:media|supports)[^{]*\{', re.I)
_COMPOUND_RE = re.compile(r'^([a-zA-Z][\w-]*|\*)?((?:[.#][\w-]+)*)$')
_ZERO_RE = re.compile(r'^0(\.0+)?[a-z%]*$')


def _flatten_at_rules(css):
    """Unwrap @media/@supports blocks so the rules inside are still seen."""
    css = _COMMENT_RE.sub('', css)
    out = []
    i = 0
    while True:
        m = _ATRULE_RE.search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        depth, j = 1, m.end()
        while j < len(css) and depth:
            if css[j] == '{':
                depth += 1
            elif css[j] == '}':
                depth -= 1
            j += 1
        out.append(css[m.end():j - 1])
        i = j
    return ''.join(out)


def parse_css(css):
    """-> list of ``(selector, {prop: (value, important)})`` in source order."""
    rules = []
    if not css:
        return rules
    for m in _RULE_RE.finditer(_flatten_at_rules(css)):
        selector_text, body = m.group(1).strip(), m.group(2)
        if not selector_text or selector_text.startswith('@'):
            continue
        decls = {}
        for part in body.split(';'):
            c = part.find(':')
            if c < 1:
                continue
            prop = part[:c].strip().lower()
            val = part[c + 1:].strip()
            if not prop or not val:
                continue
            important = bool(re.search(r'!\s*important', val, re.I))
            val = re.sub(r'!\s*important', '', val, flags=re.I).strip().lower()
            if val:
                decls[prop] = (val, important)
        if not decls:
            continue
        for sel in selector_text.split(','):
            sel = sel.strip()
            if sel:
                rules.append((sel, decls))
    return rules


def expand_box_shorthand(decls):
    """Expand ``margin``/``padding`` shorthands into their left/right longhands."""
    out = dict(decls)
    for side in ('margin', 'padding'):
        if side not in decls:
            continue
        value, important = decls[side]
        tok = value.split()
        if len(tok) == 1:
            left = right = tok[0]
        elif len(tok) in (2, 3):
            left = right = tok[1]
        else:
            right, left = tok[1], tok[3]
        out[side + '-left'] = (left, important)
        out[side + '-right'] = (right, important)
    return out


def specificity(selector):
    a = len(re.findall(r'#[\w-]+', selector))
    b = len(re.findall(r'\.[\w-]+', selector))
    c = len(re.findall(r'(?:^|\s)([a-zA-Z][\w-]*)', selector))
    return a, b, c


def _compound_matches(compound, node):
    """``None`` means the selector syntax is unsupported and the rule should be ignored."""
    m = _COMPOUND_RE.match(compound)
    if not m:
        return None
    tag = (m.group(1) or '').lower()
    if tag and tag != '*' and tag != node['tag']:
        return False
    for bit in re.findall(r'[.#][\w-]+', m.group(2) or ''):
        name = bit[1:]
        if bit[0] == '.':
            if name not in node['classes']:
                return False
        elif node['id'] != name:
            return False
    return True


def selector_matches(selector, chain):
    """Match ``selector`` against ``chain`` (root-first; the target is ``chain[-1]``).

    Only descendant combinators are understood. Anything else returns ``None`` so the caller
    can ignore the rule rather than guess at its meaning.
    """
    if re.search(r'[>+~\[:]', selector):
        return None
    parts = selector.split()
    if not parts:
        return False
    target = _compound_matches(parts[-1], chain[-1])
    if target is None:
        return None
    if not target:
        return False
    i = len(chain) - 2
    for comp in reversed(parts[:-1]):
        matched = False
        while i >= 0:
            r = _compound_matches(comp, chain[i])
            if r is None:
                return None
            if r:
                matched = True
                i -= 1
                break
            i -= 1
        if not matched:
            return False
    return True


def computed_styles(chain, rules):
    """Resolve :data:`TRACKED_PROPS` for every node in ``chain``."""
    result = []
    for idx in range(len(chain)):
        sub = chain[:idx + 1]
        matching = []
        for order, (sel, decls) in enumerate(rules):
            if selector_matches(sel, sub) is True:
                matching.append((specificity(sel), order, decls))
        matching.sort(key=lambda t: (t[0], t[1]))

        acc = {}
        for _, _, decls in matching:
            for prop, (val, important) in expand_box_shorthand(decls).items():
                if prop not in TRACKED_PROPS:
                    continue
                if prop in acc and acc[prop][1] and not important:
                    continue
                acc[prop] = (val, important)
        # an inline style attribute beats everything in the stylesheets
        for prop, (val, important) in expand_box_shorthand(chain[idx]['inline']).items():
            if prop in TRACKED_PROPS:
                acc[prop] = (val, important)
        result.append({p: v for p, (v, _imp) in acc.items()})
    return result


def is_nonzero(value):
    if not value or value in ('0', 'auto', 'inherit', 'initial', 'none'):
        return False
    return not _ZERO_RE.match(value)


# --------------------------------------------------------------------------------------
# Archive helpers
# --------------------------------------------------------------------------------------

def resolve_path(base_entry, relative):
    """Resolve ``relative`` against ``base_entry``'s folder, returning a zip entry name."""
    if not relative:
        return None
    rel = relative.split('#')[0].split('?')[0]
    if not rel or rel.startswith(('http:', 'https:', 'data:', 'mailto:')):
        return None
    try:
        rel = unquote(rel)
    except Exception:
        pass
    if rel.startswith('/'):
        return rel.lstrip('/')
    base = posixpath.dirname(base_entry)
    return posixpath.normpath(posixpath.join(base, rel)).replace('\\', '/')


def _text(zf, name):
    try:
        return zf.read(name).decode('utf-8', 'replace').lstrip('﻿')
    except KeyError:
        return None


def find_opf(zf, names):
    container = _text(zf, 'META-INF/container.xml')
    if container:
        m = re.search(r'full-path\s*=\s*"([^"]+)"', container)
        if m and m.group(1) in names:
            return m.group(1)
    for n in names:
        if n.lower().endswith('.opf'):
            return n
    return None


def opf_version(opf_text):
    m = re.search(r'<package\b[^>]*\bversion\s*=\s*"([^"]+)"', opf_text or '')
    return m.group(1) if m else '2.0'


def epub_version(path):
    """The EPUB version of a book on disk, e.g. ``'3.0'``. ``None`` if it cannot be read.

    Used to decide whether the book needs upgrading before the fixes are applied - the
    ``properties="svg"`` manifest attribute only exists in EPUB 3.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = [i.filename for i in zf.infolist() if not i.filename.endswith('/')]
            opf_name = find_opf(zf, names)
            if not opf_name:
                return None
            return opf_version(_text(zf, opf_name))
    except Exception:                                         # noqa: BLE001 - caller decides
        return None


def is_epub3(path):
    v = epub_version(path)
    return bool(v and v.startswith('3'))


def content_documents(zf, names, opf_name):
    """Content documents, driven by the OPF manifest with an extension scan as backstop.

    The manifest is authoritative (it finds documents with unusual extensions), but a page that
    is missing from the manifest should still be seen, hence the union.
    """
    found = []
    if opf_name:
        opf = _text(zf, opf_name)
        if opf:
            for m in re.finditer(r'<item\b[^>]*/?>', opf):
                item = m.group(0)
                mt = re.search(r'media-type\s*=\s*"([^"]+)"', item)
                if not mt or not re.search(r'xhtml\+xml|text/html', mt.group(1), re.I):
                    continue
                href = re.search(r'href\s*=\s*"([^"]+)"', item)
                if not href:
                    continue
                entry = resolve_path(opf_name, href.group(1))
                if entry and entry in names and entry not in found:
                    found.append(entry)
    for n in names:
        if re.search(r'\.(x?html?|xhtm)$', n.lower()) and n not in found:
            found.append(n)
    return found


def parse_xhtml(text):
    try:
        return ET.fromstring(text.encode('utf-8'))
    except ET.ParseError:
        return None


def find_cover_page(zf, names, opf_name):
    """Locate the cover page. Order matters - KFX-converted books have no titlepage.xhtml."""
    for n in names:
        if not re.search(r'\.x?html?$', n.lower()):
            continue
        t = _text(zf, n)
        if t and re.search(r'name\s*=\s*"calibre:cover"', t, re.I):
            return n

    if opf_name:
        opf = _text(zf, opf_name)
        if opf:
            # manifest item flagged as the title page (the only marker KFX output leaves)
            for m in re.finditer(r'<item\b[^>]*/?>', opf):
                item = m.group(0)
                props = re.search(r'properties\s*=\s*"([^"]*)"', item)
                if not props or 'calibre:title-page' not in props.group(1):
                    continue
                href = re.search(r'href\s*=\s*"([^"]+)"', item)
                if href:
                    e = resolve_path(opf_name, href.group(1))
                    if e and e in names:
                        return e
            g = re.search(r'<reference\b[^>]*type\s*=\s*"cover"[^>]*href\s*=\s*"([^"]+)"', opf, re.I)
            if g:
                e = resolve_path(opf_name, g.group(1))
                if e and e in names:
                    return e

    for n in names:
        if n.lower().endswith('titlepage.xhtml'):
            return n
    return None


# --------------------------------------------------------------------------------------
# Page classification
# --------------------------------------------------------------------------------------

class PageInfo(dict):
    """A classification result. ``action`` is one of rewrite / svg-repair / skip."""


def _skip(page, category, reason, dims=None):
    return PageInfo(page=page, action='skip', category=category, reason=reason, dims=dims)


def _excerpt(text, limit=48):
    t = re.sub(r'\s+', ' ', text).strip()
    return t if len(t) <= limit else t[:limit] + '...'


def classify_page(zf, entry, names, min_width_percent):
    """Classify one content document.

    Returns ``None`` only when the page holds no image at all; every image-bearing page yields a
    result, so nothing is ever dropped without a recorded reason.
    """
    text = _text(zf, entry)
    if text is None:
        return None
    has_img = re.search(r'<img\b', text, re.I) is not None
    has_svg = re.search(r'<svg\b', text, re.I) is not None
    if not has_img and not has_svg:
        return None

    root = parse_xhtml(text)
    if root is None:
        return _skip(entry, 'unparsable', 'XHTML did not parse as XML')

    body = root.find(XH + 'body')
    if body is None:
        body = root.find('body')
    if body is None:
        return None

    # ---- already an SVG page object: repair the attribute, never rewrite ----
    svgs = body.findall('.//' + SVG + 'svg')
    if svgs:
        needy = []
        for sv in svgs:
            par = sv.get('preserveAspectRatio')
            vb = sv.get('viewBox')
            if vb and par and par.strip().lower() != 'none':
                continue
            if vb and not par:
                continue  # unset defaults to xMidYMid meet, which is already correct
            needy.append(sv)
        if not needy:
            return _skip(entry, 'already-svg-ok', 'SVG page object already correct')
        if all(not sv.get('viewBox') for sv in needy):
            return _skip(entry, 'svg-no-viewbox', 'SVG has no viewBox; cannot set meet safely')
        return PageInfo(page=entry, action='svg-repair', category='svg-stretched',
                        reason='preserveAspectRatio=none on %d svg element(s)' % len(needy),
                        dims=None)

    imgs = body.findall('.//' + XH + 'img') or body.findall('.//img')
    if not imgs:
        return None

    body_text = ''.join(body.itertext()).strip()
    if body_text:
        # A short caption beside one image might be a full-page image page and is worth
        # surfacing; longer text is ordinary prose with an inline image.
        cat = ('captioned-candidate' if len(imgs) == 1 and len(body_text) <= 120 else 'has-text')
        return _skip(entry, cat, '%d image(s) + %d chars: "%s"'
                     % (len(imgs), len(body_text), _excerpt(body_text)))

    if len(imgs) != 1:
        return _skip(entry, 'multi-image',
                     '%d images on one page; rewriting would drop all but one' % len(imgs))

    img = imgs[0]
    src = img.get('src')
    if not src:
        return _skip(entry, 'no-src', '<img> has no src attribute')
    img_entry = resolve_path(entry, src)
    if not img_entry or img_entry not in names:
        return _skip(entry, 'missing-image', "src '%s' not in archive" % src)

    dims = image_size(zf.read(img_entry))
    if not dims:
        ext = posixpath.splitext(img_entry)[1]
        return _skip(entry, 'unreadable-image',
                     "could not read dimensions of '%s' (%s)" % (img_entry.split('/')[-1], ext))

    # ---- resolve the cascade over the body -> ... -> img chain ----
    rules = []
    for link in root.iter(XH + 'link'):
        rel = (link.get('rel') or '').lower()
        if rel and 'stylesheet' not in rel:
            continue
        href = link.get('href')
        if not href:
            continue
        css_entry = resolve_path(entry, href)
        if css_entry and css_entry in names:
            rules.extend(parse_css(_text(zf, css_entry)))
    for style in root.iter(XH + 'style'):
        rules.extend(parse_css(''.join(style.itertext())))

    parents = {c: p for p in body.iter() for c in p}
    chain_els, cur = [img], img
    while cur is not body and cur in parents:
        cur = parents[cur]
        chain_els.append(cur)
    chain_els.reverse()

    chain = []
    for el in chain_els:
        style_attr = el.get('style') or ''
        inline = {}
        for part in style_attr.split(';'):
            c = part.find(':')
            if c < 1:
                continue
            inline[part[:c].strip().lower()] = (part[c + 1:].strip().lower(), True)
        chain.append({
            'tag': el.tag.split('}')[-1].lower(),
            'classes': set((el.get('class') or '').split()),
            'id': el.get('id'),
            'inline': inline,
        })

    comp = computed_styles(chain, rules)
    istyle = comp[-1]
    width = istyle.get('width')
    height = istyle.get('height')

    # Percentage widths compound down the chain: a body at 39.961% holding an img at 100%
    # displays the image at ~40% of the column, not 100%. Testing only the img's own width
    # would misread deliberate ornaments as full-page images.
    effective = 1.0
    chain_parts = []
    for node, style in zip(chain, comp):
        w = style.get('width')
        if w and re.match(r'^[\d.]+%$', w):
            effective *= float(w[:-1]) / 100.0
            chain_parts.append('%s=%s' % (node['tag'], w))
    eff_pct = round(effective * 100, 2)
    chain_desc = (' [' + ' x '.join(chain_parts) + ']') if chain_parts else ''

    if width and re.match(r'^[\d.]+%$', width):
        full_page = eff_pct >= min_width_percent
        reason = 'effective width %s%%%s' % (eff_pct, chain_desc)
    elif not width or width == 'auto':
        if height and re.match(r'^[\d.]+%$', height) and float(height[:-1]) >= min_width_percent:
            full_page = eff_pct >= min_width_percent
            reason = ('height %s fit-to-height, effective width %s%%%s'
                      % (height, eff_pct, chain_desc))
        elif dims[0] >= 800 and eff_pct >= min_width_percent:
            full_page = True
            reason = 'width auto, intrinsic %dpx, effective %s%%' % (dims[0], eff_pct)
        else:
            full_page = False
            reason = 'width auto, intrinsic %dpx, effective %s%%' % (dims[0], eff_pct)
    elif re.match(r'^[\d.]+px$', width or ''):
        full_page = float(width[:-2]) >= 800 and eff_pct >= min_width_percent
        reason = 'width %s, effective %s%%' % (width, eff_pct)
    else:
        full_page = False
        reason = 'width %s, effective %s%%' % (width, eff_pct)

    if not full_page:
        return _skip(entry, 'too-narrow', reason, dims)

    overflow = []
    for node, style in zip(chain, comp):
        w = style.get('width')
        if not w or not re.match(r'^[\d.]+%$', w) or float(w[:-1]) < 100:
            continue
        ml, mr = style.get('margin-left', ''), style.get('margin-right', '')
        if (is_nonzero(ml) and ml != 'auto') or (is_nonzero(mr) and mr != 'auto'):
            overflow.append('%s width=%s margin=%s/%s' % (node['tag'], w, ml, mr))

    # Preserve every id: the TOC or page-list can link to "page.xhtml#anchor", and replacing
    # the markup wholesale would break those destinations.
    body_id = body.get('id')
    anchor_ids = [el.get('id') for el in body.iter() if el.get('id') and el.get('id') != body_id]

    return PageInfo(page=entry, action='rewrite', category='full-page-image', reason=reason,
                    src=src, img_entry=img_entry, dims=dims, overflow=overflow,
                    body_id=body_id, anchor_ids=anchor_ids, alt=img.get('alt'),
                    title=posixpath.splitext(posixpath.basename(entry))[0])


# --------------------------------------------------------------------------------------
# Rewriting
# --------------------------------------------------------------------------------------

def _xml_escape(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&apos;'))


SVG_PAGE_TEMPLATE = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">
  <head>
    <title>%(title)s</title>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
    <style type="text/css">
      @page { margin: 0; padding: 0; }
      html { margin: 0; padding: 0; height: 100%%;%(bg)s }
      body { margin: 0; padding: 0; height: 100%%; text-align: center;%(bg)s }
      div.fullpage { margin: 0; padding: 0; height: 100%%;
                     text-align: center; page-break-inside: avoid; }
      svg { display: block; margin: 0 auto; padding: 0; height: 100%%;%(bg)s }
    </style>
  </head>
  <body%(body_id)s>
<div class="fullpage">%(anchors)s<svg xmlns="http://www.w3.org/2000/svg" \
xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="100%%" height="100%%" \
viewBox="0 0 %(w)d %(h)d" preserveAspectRatio="xMidYMid meet"%(a11y)s>%(svg_title)s\
<image width="%(w)d" height="%(h)d" xlink:href="%(src)s"/></svg></div>
</body>
</html>
"""

#: id of the <title> the accessible name points at
SVG_TITLE_ID = 'eplf-img-title'


def build_svg_page(title, src, width, height, body_id=None, anchor_ids=(), preserve_anchors=True,
                   alt=None, background=None):
    """A self-contained full-page image document.

    No ``width:100%`` anywhere - these are block boxes, so auto width already fills the column;
    forcing 100% adds the reader's injected side margins on top and pushes the image right.
    No ``vh`` units either: Adobe RMSDK (Kobo, Tolino, PocketBook) ignores them silently.

    ``alt`` carries the replaced ``<img alt="...">`` across as the SVG's accessible name; without
    it every rewritten page would lose its only accessible description.
    ``background`` paints the letterbox bands, used for the cover.
    """
    anchors = ''
    body_attr = ''
    if preserve_anchors:
        if body_id:
            body_attr = ' id="%s"' % _xml_escape(body_id)
        anchors = ''.join('<span id="%s"></span>' % _xml_escape(a) for a in anchor_ids if a)

    alt = (alt or '').strip()
    a11y = ' role="img" aria-labelledby="%s"' % SVG_TITLE_ID if alt else ' role="img"'
    svg_title = ('<title id="%s">%s</title>' % (SVG_TITLE_ID, _xml_escape(alt))) if alt else ''

    bg = (' background-color: %s;' % background) if background else ''
    return SVG_PAGE_TEMPLATE % {
        'title': _xml_escape(title), 'src': _xml_escape(src),
        'w': width, 'h': height, 'body_id': body_attr, 'anchors': anchors,
        'a11y': a11y, 'svg_title': svg_title, 'bg': bg,
    }


def repair_stretched_svg(text):
    """``preserveAspectRatio="none"`` -> ``"xMidYMid meet"``, leaving the rest of the page alone.

    Only elements carrying a ``viewBox`` are touched; without one there is nothing for ``meet``
    to fit against.
    """
    def sub(m):
        tag = m.group(0)
        if not re.search(r'viewBox\s*=\s*"', tag, re.I):
            return tag
        return re.sub(r'preserveAspectRatio\s*=\s*"\s*none\s*"',
                      'preserveAspectRatio="xMidYMid meet"', tag, flags=re.I)
    out = re.sub(r'<svg\b[^>]*>', sub, text)
    return out, out != text


#: closes the block opened by COVER_MARKER, so a re-run replaces exactly what it wrote before
COVER_END_MARKER = '/' + COVER_MARKER

#: The block written by a previous run, up to but not including ``</style>``. The end marker is
#: optional because books fixed by 0.1.0 carry only the opening one; matching to the end of the
#: style element covers those, and the whole region is replaced so a re-run cannot leave two
#: conflicting rule sets behind.
_COVER_BLOCK_RE = re.compile(
    r'\n?[ \t]*/\*\s*' + re.escape(COVER_MARKER) + r'\s*\*/'
    r'.*?(?:/\*\s*' + re.escape(COVER_END_MARKER) + r'\s*\*/)?'
    r'[ \t]*\n?[ \t]*(?=</style>)', re.S | re.I)


def cover_rules(color=None):
    """The rules a patched-in-place cover page needs.

    Two problems have to be solved, and only the second one is cosmetic:

    * the page's own stylesheet reaches the ``<svg>`` through a class selector - calibre writes
      ``.calibreN { height: auto; width: auto }`` - which outranks the ``height="100%"``
      presentation attribute and leaves the cover sized by the UA default. A class selector beats
      a bare type selector, so these overrides have to be ``!important``.
    * ``@page { margin: 5pt }`` and ``body { margin: 0 5pt }`` survive from the book's stylesheet
      and put a gutter around what should be a full-bleed page.

    ``color`` additionally paints the letterbox bands.
    """
    bg = (' background-color: %s !important;' % color) if color else ''
    return ('\n            /* %s */\n'
            '            @page { margin: 0; padding: 0; }\n'
            '            html, body { margin: 0 !important; padding: 0 !important;\n'
            '                         height: 100%% !important;%s }\n'
            '            svg { display: block; margin: 0 auto !important; padding: 0;\n'
            '                  width: auto !important; height: 100%% !important;%s }\n'
            '            /* %s */\n        '
            % (COVER_MARKER, bg, bg, COVER_END_MARKER))


def set_cover_background(text, color=None):
    """Make the cover page full-bleed, optionally with letterbox bands.

    Idempotent: a previous run's block is replaced whole rather than patched, so re-running with a
    different colour cannot leave two conflicting rule sets behind.
    """
    rules = cover_rules(color)

    if COVER_MARKER in text:
        updated = _COVER_BLOCK_RE.sub(lambda _m: rules, text, count=1)
        return updated, updated != text

    m = re.search(r'</style>', text, re.I)
    if m:
        return text[:m.start()] + rules + text[m.start():], True
    m = re.search(r'</head>', text, re.I)
    if m:
        block = '  <style type="text/css">%s</style>\n  ' % rules
        return text[:m.start()] + block + text[m.start():], True
    return text, False


def add_opf_svg_property(opf_text, href):
    """Add ``properties="svg"`` to a manifest item.

    EPUB 3 only - the attribute is not part of the EPUB 2 schema. Matching is done on the
    *resolved* href so percent-encoded and ``./``-prefixed entries still match.
    """
    if not opf_version(opf_text).startswith('3'):
        return opf_text, False

    wanted = re.sub(r'^\./', '', unquote(href))
    for m in re.finditer(r'<item\b[^>]*/?>', opf_text):
        item = m.group(0)
        hm = re.search(r'href\s*=\s*"([^"]*)"', item)
        if not hm:
            continue
        if re.sub(r'^\./', '', unquote(hm.group(1))) != wanted:
            continue
        pm = re.search(r'properties\s*=\s*"([^"]*)"', item)
        if pm:
            if 'svg' in pm.group(1).split():
                return opf_text, False
            new_item = item.replace(pm.group(0), 'properties="%s svg"' % pm.group(1).strip())
        elif item.endswith('/>'):
            new_item = item[:-2].rstrip() + ' properties="svg"/>'
        else:
            new_item = item[:-1].rstrip() + ' properties="svg">'
        return opf_text[:m.start()] + new_item + opf_text[m.end():], True
    return opf_text, False


# --------------------------------------------------------------------------------------
# Archive rebuild + verification
# --------------------------------------------------------------------------------------

def write_epub(src_path, dest_path, replacements):
    """Rebuild the archive with ``mimetype`` first and STORED, everything else as it was."""
    with zipfile.ZipFile(src_path, 'r') as zin:
        infos = [i for i in zin.infolist() if not i.filename.endswith('/')]
        ordered = ([i for i in infos if i.filename == 'mimetype']
                   + [i for i in infos if i.filename != 'mimetype'])
        with zipfile.ZipFile(dest_path, 'w') as zout:
            for info in ordered:
                if info.filename == 'mimetype':
                    ctype = zipfile.ZIP_STORED
                elif info.compress_type == zipfile.ZIP_STORED:
                    ctype = zipfile.ZIP_STORED
                else:
                    ctype = zipfile.ZIP_DEFLATED
                data = replacements.get(info.filename)
                data = data.encode('utf-8') if isinstance(data, str) else zin.read(info.filename)
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = ctype
                zi.external_attr = info.external_attr
                zi.internal_attr = info.internal_attr
                zi.create_system = info.create_system
                zout.writestr(zi, data)


def verify_epub(original_path, new_path, changed):
    """Return a list of problems; empty means the rebuild is sound."""
    problems = []
    with zipfile.ZipFile(original_path) as zo, zipfile.ZipFile(new_path) as zn:
        old = {i.filename: i for i in zo.infolist() if not i.filename.endswith('/')}
        new = {i.filename: i for i in zn.infolist() if not i.filename.endswith('/')}

        missing = set(old) - set(new)
        if missing:
            problems.append('entries lost: %s' % ', '.join(sorted(missing)))
        added = set(new) - set(old)
        if added:
            problems.append('unexpected entries: %s' % ', '.join(sorted(added)))

        first = zn.infolist()[0]
        if first.filename != 'mimetype':
            problems.append("first entry is '%s', not 'mimetype'" % first.filename)
        elif first.compress_type != zipfile.ZIP_STORED:
            problems.append('mimetype is not stored uncompressed')

        names = set(new)
        for name in changed:
            if not re.search(r'\.(x?html?|opf)$', name.lower()):
                continue
            t = _text(zn, name)
            if t is None:
                problems.append('changed entry missing: %s' % name)
                continue
            if parse_xhtml(t) is None:
                problems.append('not well-formed XML: %s' % name)
            for m in re.finditer(r'xlink:href\s*=\s*"([^"]+)"', t):
                tgt = resolve_path(name, m.group(1))
                if tgt and tgt not in names:
                    problems.append('broken image ref in %s: %s' % (name, tgt))

        for name, info in old.items():
            if name in changed or name not in new:
                continue
            if new[name].CRC != info.CRC:
                problems.append('unexpectedly modified: %s' % name)
    return problems


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------

class Result(object):
    """Outcome of analysing or fixing one book."""

    def __init__(self, path):
        self.path = path
        self.image_pages = 0
        self.svg_repaired = 0
        self.cover_fixed = False
        self.skipped = 0
        self.dead_links = 0
        self.ledger = []          # dicts: page, action, category, reason, width, height
        self.details = []         # human-readable lines
        self.problems = []
        self.changed = False
        self.error = None

    @property
    def notable_skips(self):
        return [e for e in self.ledger
                if e['action'] == 'skip' and e['category'] in NOTABLE_SKIPS]

    def summary(self):
        return ('%d image page(s), %d svg repair(s), cover %s, %d dead link(s), %d skipped'
                % (self.image_pages, self.svg_repaired,
                   'fixed' if self.cover_fixed else 'unchanged', self.dead_links, self.skipped))


def _plan(zf, names, settings, result):
    """Work out every change without writing anything. -> ``{entry: new_text}``"""
    replacements = {}
    opf_name = find_opf(zf, names)
    opf_text = _text(zf, opf_name) if opf_name else None
    opf_dirty = False

    cover_name = find_cover_page(zf, names, opf_name) if settings['fix_covers'] else None

    if settings['fix_images']:
        for entry in content_documents(zf, names, opf_name):
            if cover_name and entry == cover_name:
                continue
            info = classify_page(zf, entry, names, settings['min_width_percent'])
            if info is None:
                continue

            result.ledger.append({
                'page': entry, 'action': info['action'], 'category': info['category'],
                'reason': info['reason'],
                'width': (info.get('dims') or (None, None))[0],
                'height': (info.get('dims') or (None, None))[1],
            })

            if info['action'] == 'skip':
                result.skipped += 1
                if info['category'] in NOTABLE_SKIPS:
                    dim = ' [%dx%d]' % info['dims'] if info.get('dims') else ''
                    result.details.append('skip %s [%s] %s%s'
                                          % (entry.split('/')[-1], info['category'],
                                             info['reason'], dim))
                continue

            if info['action'] == 'svg-repair':
                new_text, changed = repair_stretched_svg(_text(zf, entry))
                if changed:
                    replacements[entry] = new_text
                    result.svg_repaired += 1
                    result.details.append('svg  %s preserveAspectRatio none -> xMidYMid meet'
                                          % entry.split('/')[-1])
                continue

            w, h = info['dims']
            replacements[entry] = build_svg_page(
                info['title'], info['src'], w, h,
                info.get('body_id'), info.get('anchor_ids') or (),
                settings['preserve_anchors'], alt=info.get('alt'))
            result.image_pages += 1
            kept = len(info.get('anchor_ids') or ()) + (1 if info.get('body_id') else 0)
            result.details.append(
                'fix  %s [%dx%d] %s%s%s'
                % (entry.split('/')[-1], w, h, info['reason'],
                   ('  OVERFLOW: ' + '; '.join(info['overflow'])) if info['overflow'] else '',
                   (' (kept %d anchor id(s))' % kept) if kept else ''))

            if opf_text:
                opf_text, changed = _mark_svg_page(opf_text, opf_name, entry)
                opf_dirty = opf_dirty or changed

    # ---- cover ----
    if cover_name and _fix_cover(zf, cover_name, names, settings, result, replacements):
        if opf_text:
            opf_text, changed = _mark_svg_page(opf_text, opf_name, cover_name)
            opf_dirty = opf_dirty or changed

    # ---- navigation: drop links calibre left pointing at pages it deleted ----
    _repair_navigation(zf, names, opf_name, result, replacements)

    if opf_dirty:
        replacements[opf_name] = opf_text

    return replacements


def nav_documents(zf, names, opf_name):
    """The EPUB 3 navigation documents, from the manifest ``properties="nav"`` flag."""
    found = []
    opf = _text(zf, opf_name) if opf_name else None
    if opf:
        for m in re.finditer(r'<item\b[^>]*/?>', opf):
            item = m.group(0)
            props = re.search(r'properties\s*=\s*"([^"]*)"', item)
            if not props or 'nav' not in props.group(1).split():
                continue
            href = re.search(r'href\s*=\s*"([^"]+)"', item)
            if href:
                e = resolve_path(opf_name, href.group(1))
                if e and e in names and e not in found:
                    found.append(e)
    if not found:
        for n in names:
            if n.lower().endswith('nav.xhtml'):
                found.append(n)
    return found


def repair_nav_links(text, entry, names):
    """Neutralise references whose target is not in the archive.

    calibre's conversion replaces the publisher's cover page with a generated title page but
    leaves the navigation document pointing at the file it deleted, so "Cover" is a dead entry in
    the table of contents. A dangling link becomes a ``<span>``, which is what EPUB 3 expects for
    an unlinked heading; in the landmarks list, where a bare ``<span>`` is not allowed, the whole
    entry goes.

    A ``<link rel="stylesheet">`` pointing at a stylesheet that was trimmed from the book is the
    same defect wearing different clothes - calibre leaves those behind too - and is simply
    removed, since a reader cannot load it either way.
    """
    dropped = []

    def target_missing(href):
        tgt = resolve_path(entry, href.split('#')[0])
        return bool(tgt) and tgt not in names

    def external(href):
        return href.startswith('#') or bool(re.match(r'^[a-z][a-z0-9+.-]*:', href, re.I))

    # ---- dead <link> elements ----
    def drop_link(m):
        href = re.search(r'href\s*=\s*"([^"]*)"', m.group(0))
        if not href or external(href.group(1)) or not target_missing(href.group(1)):
            return m.group(0)
        dropped.append(href.group(1))
        return ''

    text = re.sub(r'\n?[ \t]*<link\b[^>]*/?>', drop_link, text, flags=re.I)

    # ---- dead <a href> links ----
    out, pos, changed = [], 0, bool(dropped)
    for m in re.finditer(r'<a\b[^>]*?href\s*=\s*"([^"]+)"[^>]*>', text, re.I):
        href = m.group(1)
        if external(href) or not target_missing(href):
            continue

        close = text.find('</a>', m.end())
        if close < 0:
            continue
        # landmarks entries are meaningless without a destination - drop the list item whole
        li_open = text.rfind('<li', 0, m.start())
        li_close = text.find('</li>', close)
        is_landmark = 'epub:type' in m.group(0)
        if is_landmark and li_open >= 0 and li_close >= 0:
            start, end = li_open, li_close + len('</li>')
            while start > 0 and text[start - 1] in ' \t':       # take the indent with it
                start -= 1
            if start > 0 and text[start - 1] == '\n':
                start -= 1
        else:
            start, end = None, None

        if start is None:
            out.append(text[pos:m.start()])
            out.append('<span>')
            out.append(text[m.end():close])
            out.append('</span>')
            pos = close + len('</a>')
        else:
            if start < pos:                                    # overlapping match, leave it alone
                continue
            out.append(text[pos:start])
            pos = end
        dropped.append(href)
        changed = True

    if not changed:
        return text, []
    if not out:
        return text, dropped                                   # only <link> elements were dropped
    out.append(text[pos:])
    return ''.join(out), dropped


def _repair_navigation(zf, names, opf_name, result, replacements):
    """Every content document, not only the nav: a reference to a file calibre trimmed is the
    same defect wherever it appears."""
    seen = set()
    for nav in list(nav_documents(zf, names, opf_name)) + content_documents(zf, names, opf_name):
        if nav in seen:
            continue
        seen.add(nav)
        text = replacements.get(nav) or _text(zf, nav)
        if not text:
            continue
        fixed, dropped = repair_nav_links(text, nav, names)
        if not dropped:
            continue
        replacements[nav] = fixed
        result.dead_links += len(dropped)
        result.details.append('link %s dropped %d reference(s) to missing file(s): %s'
                              % (nav.split('/')[-1], len(dropped), ', '.join(sorted(set(dropped)))))
        result.ledger.append({'page': nav, 'action': 'link-repair', 'category': 'dangling-link',
                              'reason': 'targets not in the archive: %s' % ', '.join(dropped),
                              'width': None, 'height': None})


def _mark_svg_page(opf_text, opf_name, entry):
    """``properties="svg"`` on the manifest item for ``entry``, addressed OPF-relative."""
    opf_dir = posixpath.dirname(opf_name)
    href = (entry[len(opf_dir) + 1:]
            if opf_dir and entry.startswith(opf_dir + '/') else entry)
    return add_opf_svg_property(opf_text, href)


def _fix_cover(zf, cover_name, names, settings, result, replacements):
    """Repair the cover page.

    A cover built from a plain ``<img>`` is rebuilt from the same template as any other full-page
    image, which is the stronger fix: the result carries no stylesheet links, so nothing from the
    book's own CSS can reach it. A cover that is already an SVG page object is patched in place
    instead - rewriting it would discard whatever the producer put there - and gets explicit
    overrides for the rules that otherwise defeat it.

    -> True when the page was rebuilt, so the caller can mark it ``properties="svg"``.
    """
    cover_text = _text(zf, cover_name)
    if not cover_text:
        return False
    colour = settings['cover_color'] if settings['dark_cover'] else None

    info = classify_page(zf, cover_name, names, 0.0)
    if info is not None and info['action'] == 'rewrite':
        w, h = info['dims']
        replacements[cover_name] = build_svg_page(
            info['title'], info['src'], w, h,
            info.get('body_id'), info.get('anchor_ids') or (),
            settings['preserve_anchors'], alt=info.get('alt'), background=colour)
        result.image_pages += 1
        result.cover_fixed = bool(colour)
        result.details.append('fix  %s (cover) [%dx%d] rebuilt as a full-page image'
                              % (cover_name.split('/')[-1], w, h))
        result.ledger.append({'page': cover_name, 'action': 'rewrite', 'category': 'cover-image',
                              'reason': 'cover rebuilt from the full-page template',
                              'width': w, 'height': h})
        return True

    dirty = False
    repaired, changed = repair_stretched_svg(cover_text)
    if changed:
        cover_text, dirty = repaired, True
        result.svg_repaired += 1
        result.details.append('svg  %s (cover) preserveAspectRatio none -> xMidYMid meet'
                              % cover_name.split('/')[-1])
        result.ledger.append({'page': cover_name, 'action': 'svg-repair',
                              'category': 'cover-stretched',
                              'reason': 'preserveAspectRatio=none on cover',
                              'width': None, 'height': None})

    # Always applied, colour or not: the sizing and margin overrides are the actual repair, and
    # without them the book's own .calibreN rule keeps the cover at its UA default size.
    styled, changed = set_cover_background(cover_text, colour)
    if changed:
        cover_text, dirty = styled, True
        result.cover_fixed = bool(colour)
        result.details.append('css  %s (cover) full-bleed overrides%s'
                              % (cover_name.split('/')[-1],
                                 ' + letterbox %s' % colour if colour else ''))
    if dirty:
        replacements[cover_name] = cover_text
    return False


def analyze_epub(path, settings=None):
    """Classify a book without writing anything."""
    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    result = Result(path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = [i.filename for i in zf.infolist() if not i.filename.endswith('/')]
            replacements = _plan(zf, names, settings, result)
        result.changed = bool(replacements)
    except Exception as e:                                    # noqa: BLE001 - reported, not raised
        result.error = '%s: %s' % (type(e).__name__, e)
    return result


def fix_epub(path, settings=None, dest=None):
    """Apply the fixes. Writes in place unless ``dest`` is given.

    The rebuilt archive is verified before it replaces anything; on failure the original is left
    untouched and the problems are reported on the result.
    """
    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    result = Result(path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = [i.filename for i in zf.infolist() if not i.filename.endswith('/')]
            replacements = _plan(zf, names, settings, result)

        if not replacements:
            return result

        target = dest or path
        tmp = target + '.eplf-tmp'
        try:
            write_epub(path, tmp, replacements)
            result.problems = verify_epub(path, tmp, set(replacements))
            if result.problems:
                return result
            if os.path.exists(target):
                os.remove(target)
            os.replace(tmp, target)
            result.changed = True
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception as e:                                    # noqa: BLE001 - reported, not raised
        result.error = '%s: %s' % (type(e).__name__, e)
    return result
