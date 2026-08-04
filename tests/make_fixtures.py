"""Build synthetic EPUBs covering cases the real library does not contain."""
import os
import shutil
import struct
import zipfile
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)

CSS = """
.full { display:block; width:100%; margin:0 5pt; }
.imgfull { width:100%; height:auto; }
.small { display:block; width:35%; margin:0 5pt; }
"""


def png(w, h):
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def webp_vp8l(w, h):
    """Minimal VP8L WebP header - only the dimensions need to be readable."""
    bits = (w - 1) | ((h - 1) << 14)
    payload = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)[:4]
    body = b"WEBP" + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def svg_img(w, h):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d"><rect width="%d" '
            'height="%d"/></svg>' % (w, h, w, h)).encode()


def page(body, head_extra=""):
    return ("""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
  <head><title>p</title>
    <link rel="stylesheet" type="text/css" href="style.css"/>%s
  </head>
  <body class="full">
%s
  </body>
</html>
""" % (head_extra, body))


def opf(version, items, spine):
    man = "\n".join('    <item id="%s" href="%s" media-type="%s"/>' % i for i in items)
    sp = "\n".join('    <itemref idref="%s"/>' % s for s in spine)
    return """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="%s" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Fixture</dc:title><dc:language>en</dc:language>
    <dc:identifier id="uid">urn:uuid:fixture</dc:identifier>
  </metadata>
  <manifest>
%s
  </manifest>
  <spine>
%s
  </spine>
</package>
""" % (version, man, sp)


def build(name, files, first_stored=True):
    p = os.path.join(OUT, name)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                   "</rootfiles></container>")
        for n, data in files.items():
            z.writestr(n, data)
    return p


# ---- 1. EPUB 2: properties="svg" must NOT be added ----
build("epub2.epub", {
    "style.css": CSS,
    "map.png": png(1200, 1800),
    "p1.xhtml": page('<div><img src="map.png" class="imgfull" alt=""/></div>'),
    "content.opf": opf("2.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("img", "map.png", "image/png")], ["p1"]),
})

# ---- 2. percent-encoded manifest href ----
build("pcthref.epub", {
    "style.css": CSS,
    "map.png": png(1200, 1800),
    "a b.xhtml": page('<div><img src="map.png" class="imgfull" alt=""/></div>'),
    "content.opf": opf("3.0", [("p1", "a%20b.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("img", "map.png", "image/png")], ["p1"]),
})

# ---- 3. WebP and SVG images ----
build("webpsvg.epub", {
    "style.css": CSS,
    "a.webp": webp_vp8l(1000, 1500),
    "b.svg": svg_img(900, 1400),
    "p1.xhtml": page('<div><img src="a.webp" class="imgfull" alt=""/></div>'),
    "p2.xhtml": page('<div><img src="b.svg" class="imgfull" alt=""/></div>'),
    "content.opf": opf("3.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("p2", "p2.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("i1", "a.webp", "image/webp"),
                               ("i2", "b.svg", "image/svg+xml")], ["p1", "p2"]),
})

# ---- 4. unknown image format -> must be reported, not silently dropped ----
build("badimg.epub", {
    "style.css": CSS,
    "x.jxr": b"\x00\x01\x02\x03" * 64,
    "p1.xhtml": page('<div><img src="x.jxr" class="imgfull" alt=""/></div>'),
    "content.opf": opf("3.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("i1", "x.jxr", "image/vnd.ms-photo")], ["p1"]),
})

# ---- 5. existing SVG page object with preserveAspectRatio="none" ----
stretched = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
  <head><title>p</title></head>
  <body><div><svg xmlns="http://www.w3.org/2000/svg"
    xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="100%" height="100%"
    viewBox="0 0 1200 1800" preserveAspectRatio="none">
    <image width="1200" height="1800" xlink:href="map.png"/></svg></div></body>
</html>
"""
build("svgnone.epub", {
    "map.png": png(1200, 1800),
    "p1.xhtml": stretched,
    "content.opf": opf("3.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("img", "map.png", "image/png")], ["p1"]),
})

# ---- 6. two images, no text ----
build("twoimg.epub", {
    "style.css": CSS,
    "a.png": png(1200, 1800), "b.png": png(600, 900),
    "p1.xhtml": page('<div><img src="a.png" class="imgfull" alt=""/></div>'
                     '<div><img src="b.png" class="imgfull" alt=""/></div>'),
    "content.opf": opf("3.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("i1", "a.png", "image/png"), ("i2", "b.png", "image/png")], ["p1"]),
})

# ---- 7. image + heading text ----
build("caption.epub", {
    "style.css": CSS,
    "a.png": png(1200, 1800),
    "p1.xhtml": page('<h1>READ THE FIRST CHAPTER OF</h1>'
                     '<div><img src="a.png" class="imgfull" alt=""/></div>'),
    "content.opf": opf("3.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("i1", "a.png", "image/png")], ["p1"]),
})

# ---- 8. anchors that the TOC links to ----
build("anchors.epub", {
    "style.css": CSS,
    "a.png": png(1200, 1800),
    "p1.xhtml": page('<div id="wrap"><span id="page_42"></span>'
                     '<img src="a.png" class="imgfull" alt="" id="theimg"/></div>'),
    "content.opf": opf("3.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("i1", "a.png", "image/png")], ["p1"]),
})

# ---- 9. deliberately small image (must be left alone) ----
small = page('<div><img src="a.png" class="imgfull" alt=""/></div>').replace(
    'class="full"', 'class="small"')
build("smallimg.epub", {
    "style.css": CSS,
    "a.png": png(700, 900),
    "p1.xhtml": small,
    "content.opf": opf("3.0", [("p1", "p1.xhtml", "application/xhtml+xml"),
                               ("css", "style.css", "text/css"),
                               ("i1", "a.png", "image/png")], ["p1"]),
})

for f in sorted(os.listdir(OUT)):
    print("  built", f)
print("fixtures ->", OUT)
