# EPUB Layout Fix

A calibre plugin that repairs full-page images and covers in EPUB files so they display correctly
on e-readers — particularly Kobo, Tolino and PocketBook, which use the Adobe RMSDK renderer.

Select books, click a button, and the work happens in the background as a normal calibre job.

---

## What it fixes

### Full-page images clipped at the edge

A page whose image sits inside a box at `width: 100%` that *also* carries side margins measures
wider than the reader's column, so the reader clips the overflow:

```css
body { width: 100%; margin: 0 5pt; }   /* 100% + 10pt = wider than the column */
img  { width: 100%; }
```

The cause is a collision, not a single bug: the `width: 100%` comes from the book (often from the
KFX Input plugin translating Amazon's layout), while the `5pt` margins come from calibre's own
**Page setup** conversion settings. Neither is wrong alone.

### Full-page images that overrun the bottom of the page

An image can fit horizontally and still be too tall. A 1200×1729 map at `width: 100%` on a Kobo
Clara renders about 1545px tall in roughly 1448px of usable height — around 7% too tall, so it
clips or spills onto a second page.

### Stretched covers

calibre writes `preserveAspectRatio="none"` into its generated title page unless *Preserve cover
aspect ratio* is enabled, which distorts every cover it produces.

### Bright letterbox bands

Once a cover is correctly fitted, the leftover bands take the page background — white by default.

---

## How it fixes them

Qualifying pages are rewritten as a self-contained SVG page object, so the **SVG renderer** fits the
image in both dimensions rather than the CSS cascade:

```xhtml
<div class="fullpage"><svg width="100%" height="100%" viewBox="0 0 1200 1729"
     preserveAspectRatio="xMidYMid meet">
  <image width="1200" height="1729" xlink:href="map.jpg"/>
</svg></div>
```

Three details matter, all learned the hard way on a real Kobo:

- **No `width: 100%` anywhere.** These are block boxes, so auto width already fills the column;
  forcing `100%` adds the reader's own injected margins on top and pushes the image off-centre.
- **No `vh` units.** Adobe RMSDK ignores them silently, so a `max-height: 95vh` cap is dead code on
  exactly the readers that need it most.
- **The book's stylesheets are not linked** on the rewritten page, so nothing in the book can
  override the result.

Existing SVG pages that merely carry `preserveAspectRatio="none"` are repaired in place rather than
rewritten, covers included.

---

## Why a plugin rather than just settings

Two of the four causes *are* fixable with calibre settings, and you should set them regardless:

| Problem | Fixable by a setting? |
|---|---|
| Stretched covers | **Yes** — Preferences → Conversion → Output options → EPUB output → *Preserve cover aspect ratio* |
| Horizontal clipping | **Yes** — Preferences → Conversion → Common options → Page setup → set left/right margins to 0 |
| Vertical fit | **No** — needs the image fitted in both dimensions; `max-height` needs a definite ancestor height and `vh` is ignored by RMSDK |
| Dark letterbox | **No** — hardcoded in calibre's cover template, and Extra CSS never reaches the generated title page |

The plugin also repairs books already on disk, which no forward-looking setting can do.

---

## Requirements

- calibre 5.0 or newer (developed and tested against 9.12.0)
- The [KFX Input](https://www.mobileread.com/forums/showthread.php?t=291290) plugin, only if you
  want to convert from KFX

## Installation

Build the zip, then load it:

```
python build.py
```

Then in calibre: **Preferences → Plugins → Load plugin from file** → `dist/EPUB-Layout-Fix.zip`.

Or from the command line:

```
calibre-customize -a dist/EPUB-Layout-Fix.zip
```

Restart calibre, then place the buttons via **Preferences → Toolbars & menus**.

## Usage

Three actions are contributed, each placeable independently:

| Action | What it does |
|---|---|
| **EPUB Layout Fix** | Opens the settings window, then repairs the selected books' existing EPUB |
| **EPUB Layout Fix - quick run** | Repairs immediately with the stored settings, no dialog |
| **EPUB Layout Fix - convert and fix** | calibre's own conversion window with **Polish** and **Layout fixes** panels added; converts, polishes, then repairs |

The conversion window is calibre's real one — Metadata, Look & feel, Page setup and the rest are
unchanged — with two extra categories in the left-hand list.

## Settings

| Option | Default | Effect |
|---|---|---|
| Rewrite full-page images | on | Rebuild qualifying pages as SVG page objects |
| Treat as full-page from | 80% | How wide an image must actually display to qualify |
| Preserve anchor ids | on | Carry `id` attributes across so TOC and page-list links keep working |
| Repair stretched covers | on | `preserveAspectRatio="none"` → `xMidYMid meet` |
| Dark letterbox bands | on | Paint the bands around the cover |
| Letterbox colour | `#000000` | Any hex colour |
| Target EPUB version | EPUB 3 | EPUB 3 upgrades book internals first; EPUB 2 leaves the version alone |
| Polish | calibre's own settings | calibre's polish operations, run before the layout fixes |

The Polish panel is built from calibre's own operation list at runtime, so it always matches the
Polish book dialog rather than drifting from a hardcoded copy.

## How detection works

Detection is **structural and never based on class names**, which is what makes it work on books
from any producer. For each candidate page the plugin resolves the real CSS cascade — linked
stylesheets, `<style>` blocks and inline attributes, with proper specificity — to determine how wide
the image is *actually displayed*.

Percentage widths **compound down the ancestor chain**. A `body` at `39.961%` holding an `img` at
`100%` displays that image at about 40% of the column, not 100%. Testing only the image's own width
would mistake deliberate ornaments for full-page art.

A page qualifies when its `<body>` contains no text and exactly one `<img>`, it is not already
SVG-wrapped, and the effective width is at least the configured threshold.

Every image-bearing page that is *not* rewritten is recorded with a reason, so a skip is
explainable rather than mysterious:

| Category | Meaning |
|---|---|
| `has-text` | Ordinary prose with an inline image |
| `captioned-candidate` | One image plus a short caption — reported, not converted, so the text survives |
| `multi-image` | More than one image; rewriting would drop all but one |
| `too-narrow` | Displayed below the threshold — a deliberate ornament |
| `unreadable-image` | Image format whose dimensions could not be read |
| `already-svg-ok` | Already a correct SVG page object |

## Undo

Every modified book gets an **`ORIGINAL_EPUB`** format, created with the same calibre call the
Polish book action uses. calibre's own **Restore original** menu entry reverts the change. No `.bak`
files are left anywhere.

## Known limitations

- **The fix does not survive a KFX round trip.** KFX has no SVG page concept, so converting a fixed
  EPUB to KFX and back reintroduces the defect (in a slightly different shape). Re-run the fix
  afterwards. A measured round trip also loses the page-list — 492 entries in the book tested — and
  replaces the real ASIN.
- **Captioned image pages are reported, not converted**, to avoid destroying the caption text.
- **Multi-image pages are skipped** for the same reason.
- Going via AZW3 (`kfx → azw3 → epub`) is worse than converting directly: it fragments chapters and
  loses the same metadata. Convert KFX → EPUB directly.

## Building and testing

```
python build.py                                   # -> dist/EPUB-Layout-Fix.zip
python build.py --install                         # also runs calibre-customize -a

python tests/test_fixer.py [reference-library]    # engine: fixtures, parity, idempotency
calibre-debug tests/smoke_gui.py                  # Qt widgets, offscreen
calibre-debug tests/test_pipeline.py [book ...]   # convert -> polish -> upgrade -> fix
```

The engine imports nothing from calibre or Qt, so `test_fixer.py` runs under a plain interpreter.
Its parity test asserts identical results to the PowerShell implementation this was ported from,
across the same 21-book library.

## Credits

The detection rules were derived by measuring a 996-book library rather than guessed — including
the 80% threshold, which separates genuine full-page art (90–100%) from deliberate ornaments
(around 40%).

## Licence

GPL-3.0 — see [LICENSE](LICENSE). calibre is GPL v3 and this plugin imports its modules.
