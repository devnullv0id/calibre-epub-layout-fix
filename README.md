# EPUB Layout Fix

A calibre plugin that repairs full-page images and covers in EPUB files so they display correctly
on e-readers. Mainly aimed at Kobo, Tolino and PocketBook, which use the Adobe RMSDK renderer.

Select books, click a button, and the work runs in the background as a normal calibre job.

## Install

Download **`EPUB-Layout-Fix.zip`** from the latest release, on
[GitHub](https://github.com/devnullv0id/calibre-epub-layout-fix/releases/latest) or
[Forgejo](https://code.private-home-network.de/devnullv0id/calibre-epub-layout-fix/releases/latest).
The two forges carry the same zip. Then in calibre:

1. **Preferences → Plugins → Load plugin from file**
2. Pick the zip. calibre will warn that plugins are code from an untrusted source; accept it.
3. Restart calibre.
4. Put the buttons where you want them: **Preferences → Toolbars & menus**, pick a toolbar, and add
   the **EPUB Layout Fix** entries. Nothing appears until you do this.

Or from the command line, with calibre closed:

```
calibre-customize -a EPUB-Layout-Fix.zip
```

Upgrading is the same steps; calibre replaces the old version. To remove it,
**Preferences → Plugins → EPUB Layout Fix → Remove plugin**.

Every push also builds the zip, so the newest code is the `EPUB-Layout-Fix` artifact on the most
recent green run — under Actions on
[GitHub](https://github.com/devnullv0id/calibre-epub-layout-fix/actions) or
[Forgejo](https://code.private-home-network.de/devnullv0id/calibre-epub-layout-fix/actions) — which
can be ahead of the last release.

**Requirements:** calibre 5.0 or newer (developed against 9.12.0), plus the
[KFX Input](https://www.mobileread.com/forums/showthread.php?t=291290) plugin if you want to
convert from KFX. Building from source is in [DEVELOPING.md](DEVELOPING.md).

## What it fixes

**Full-page images clipped at the edge.** A page whose image sits in a box at `width: 100%` that
also has side margins measures wider than the reader's column, so the reader clips the overflow:

```css
body { width: 100%; margin: 0 5pt; }   /* 100% + 10pt = wider than the column */
img  { width: 100%; }
```

This is a collision rather than a single bug. The `width: 100%` comes from the book, often from the
KFX Input plugin translating Amazon's layout; the `5pt` margins come from calibre's Page setup
options. Neither is wrong on its own.

**Images that overrun the bottom of the page.** An image can fit horizontally and still be too
tall. A 1200×1729 map at `width: 100%` on a Kobo Clara renders about 1545px tall in roughly 1448px
of usable height, so it clips or spills onto a second page.

**Stretched covers.** calibre writes `preserveAspectRatio="none"` into its generated title page
unless *Preserve cover aspect ratio* is enabled, which distorts every cover it produces.

**Bright letterbox bands.** Once a cover is correctly fitted, the leftover bands take the page
background, which is white by default.

### How

Qualifying pages are rewritten as a self-contained SVG page object, so the SVG renderer fits the
image in both dimensions instead of the CSS cascade:

```xhtml
<div class="fullpage"><svg width="100%" height="100%" viewBox="0 0 1200 1729"
     preserveAspectRatio="xMidYMid meet">
  <image width="1200" height="1729" xlink:href="map.jpg"/>
</svg></div>
```

Three details, all found by testing on a real Kobo: no `width: 100%` anywhere, since these are
block boxes that already fill the column and forcing `100%` adds the reader's injected margins on
top; no `vh` units, which RMSDK ignores silently; and no link to the book's stylesheets, so nothing
in the book can override the result.

Existing SVG pages carrying only `preserveAspectRatio="none"` are repaired in place rather than
rewritten. That includes covers.

### Why a plugin rather than just settings

Two of the four causes are fixable in calibre, and you should set them anyway:

| Problem | Fixable by a setting? |
|---|---|
| Stretched covers | Yes. Preferences → Conversion → Output options → EPUB output → *Preserve cover aspect ratio* |
| Horizontal clipping | Yes. Preferences → Conversion → Common options → Page setup → left/right margins to 0 |
| Vertical fit | No. Needs the image fitted in both dimensions; `max-height` needs a definite ancestor height and `vh` is ignored by RMSDK |
| Dark letterbox | No. Hardcoded in calibre's cover template, and Extra CSS never reaches the generated title page |

No forward-looking setting repairs the books already on disk.

## Usage

Four actions, each placeable independently:

| Action | What it does |
|---|---|
| **EPUB Layout Fix** | Opens the settings window, then repairs the selected books' existing EPUB |
| **EPUB Layout Fix - quick run** | Repairs immediately with the stored settings, no dialog |
| **EPUB Layout Fix - convert and fix** | calibre's own conversion window with **Polish** and **Layout fixes** panels added; converts, polishes, then repairs |
| **EPUB Layout Fix - report** | Lists what would be changed, page by page, and writes nothing |

The conversion window is calibre's real one, with Metadata, Look & feel, Page setup and the rest
unchanged, plus two extra categories in the left-hand list. Selecting more than one book opens
calibre's *Bulk convert* window instead, the same choice calibre's own Convert action makes. With
**Use saved conversion settings for individual books** ticked, each book's saved settings fill in
what the bulk window cannot specify and the window's settings layer on top. The plugin reads those
saved settings but never writes them back.

### Report, dry run and flags

**Report** runs the same detection as a real run and writes nothing, giving one row per book that
expands to one row per page examined: what was done or skipped, the category, the image size and
the reason. **Export CSV** writes the lot to a file. Useful for answering "which of these 900 books
actually need work?" before letting the plugin near them.

**Dry run** goes further: it does the entire job — convert, polish, upgrade, beautify, repair,
verify — on a temporary copy and then discards it, so it answers "what would a real run *produce*?"
rather than "what does this EPUB look like now?". It says so everywhere it could be mistaken for a
real run: the job name, each stage in the Status column, the finished state (*Dry run - discarded*)
and the summary. It is a stored setting, so it stays on until you turn it off; **Fix layout (dry
run)** in the main action's menu does one without touching the setting.

Either one puts a **red pin** against every book that would change, using calibre's marked-books
mechanism, so `marked:needs-fix` lists them. The pin comes off once a real run has fixed the book.
Marks made by anything else are left alone. After the summary you are asked whether to narrow the
library to the flagged books; it defaults to no and carries calibre's *Ask this again* checkbox.

### Fixing books automatically as they arrive

**Preferences → Plugins → Customize → Automatic** repairs books as they are added. Off by default.
It hooks calibre's database event stream rather than the *Add books* action, so every route in
counts: Add books, drag and drop, the content server, a connected device, a watched folder. Books
arriving in another format are converted to EPUB and repaired, and the original format is kept.
Events are debounced, so adding fifty books produces one run and one summary rather than fifty
dialogs. A book is never processed twice: an existing `ORIGINAL_EPUB` means it has been done.

*Embed referenced fonts* and *Download external resources* are forced off for automatic runs
whatever the Polish panel says. The first scans your computer for fonts and copies them into the
book, the second fetches remote URLs. Neither belongs in something that fires unattended.

## Settings

| Option | Default | Effect |
|---|---|---|
| Rewrite full-page images | on | Rebuild qualifying pages as SVG page objects |
| Treat as full-page from | 80% | How wide an image must actually display to qualify |
| Preserve anchor ids | on | Carry `id` attributes across so TOC and page-list links keep working |
| Also fix captioned pages | off | One image plus a caption of up to 120 characters, caption kept below |
| Also fix multi-image pages | off | Two or more images and no text, stacked and sharing the height |
| Repair stretched covers | on | `preserveAspectRatio="none"` → `xMidYMid meet` |
| Dark letterbox bands | on | Paint the bands around the cover. Off states `transparent`, so it also clears a background the book already had |
| Letterbox colour | `#000000` | Any hex colour |
| Target EPUB version | EPUB 3 | EPUB 3 upgrades book internals first; EPUB 2 leaves the version alone |
| Polish | calibre's own settings | calibre's polish operations, run before the layout fixes |
| Beautify all files | off | Pretty-print the book's source, cosmetic only |
| Dry run | off | Do all the work, then discard the result instead of saving it |
| Fix books automatically | off | Repair books as they are added to the library |
| Convert other formats | on | Automatic runs convert non-EPUB books, keeping the original format |
| Wait for the import to finish | 3 s | How long to collect an import before starting one batch |
| Ask before processing more than | 100 | Confirm once before queueing a very large import |

The Polish panel is built from calibre's operation list at runtime, so it always matches the Polish
book dialog instead of drifting from a hardcoded copy.

**Beautify all files** runs calibre's own *Beautify all files* over every content document,
stylesheet and the OPF. It cannot change rendering — calibre re-indents an element only when every
child is a block-level tag with a whitespace-only tail — but it rewrites every file, so a book that
needed no repair still comes out changed and still gets an `ORIGINAL_EPUB`. Leave it off unless you
edit books by hand.

### Metadata and the cover

When the plugin converts, it hands calibre the book's library metadata the same way calibre's own
Convert action does, so the author's display name, the author order, the sort names and the
`calibre:<uuid>` identifier survive into the EPUB. Without that step the output keeps whatever the
source file had embedded, which is how books end up shelved under "Rowling, J.K." with a sort name
of "Unknown".

The library cover is deliberately *not* passed to the conversion. Supplying one makes calibre
replace the publisher's cover page with a lower-resolution regenerated title page, then leave the
navigation pointing at the page it just deleted, so "Cover" becomes a dead TOC entry. Books that
already have that defect get those links repaired on the next run, along with any
`<link rel="stylesheet">` pointing at a stylesheet calibre trimmed.

## How detection works

Detection is structural and never looks at class names, which is what makes it work on books from
any producer. For each candidate page the plugin resolves the real CSS cascade (linked stylesheets,
`<style>` blocks and inline attributes, with specificity) to work out how wide the image actually
displays. Percentage widths compound down the ancestor chain: a `body` at `39.961%` holding an
`img` at `100%` displays that image at about 40% of the column, so testing the image's own width
alone would mistake ornaments for full-page art.

A page qualifies when its `<body>` contains no text and exactly one `<img>`, it is not already
SVG-wrapped, and the effective width is at least the configured threshold. Every image-bearing page
that is *not* rewritten is recorded with a reason:

| Category | Meaning |
|---|---|
| `has-text` | Ordinary prose with an inline image |
| `captioned-candidate` | One image plus a short caption. Reported, not converted, so the text survives |
| `multi-image` | More than one image; rewriting would drop all but one |
| `too-narrow` | Displayed below the threshold, so probably a deliberate ornament |
| `unreadable-image` | Image format whose dimensions could not be read |
| `already-svg-ok` | Already a correct SVG page object |

The 80% threshold comes from measuring a 996-book library: genuine full-page art lands at 90–100%,
deliberate ornaments around 40%.

## Command line

calibre hands a plugin the command line through `calibre-debug -r`. No GUI is involved, and the
pipeline is the same one the buttons run.

```
calibre-debug -r "EPUB Layout Fix" -- --help

# what would change, without touching anything
calibre-debug -r "EPUB Layout Fix" -- --report -v book.epub

# repair a tree of books in place, keeping each original as .bak
calibre-debug -r "EPUB Layout Fix" -- --recursive --backup ~/Books

# convert and repair, leaving the sources alone
calibre-debug -r "EPUB Layout Fix" -- --convert --output-dir out/ *.azw3

# or work on a library, with calibre closed
calibre-debug -r "EPUB Layout Fix" -- --library ~/Calibre --search "formats:EPUB" --report
calibre-debug -r "EPUB Layout Fix" -- --library ~/Calibre --ids 41,42
```

A directory yields its EPUBs; with `--convert` it also yields KFX, AZW3, MOBI, AZW, KEPUB, DOCX,
FB2 and PDF, skipping any that already has an EPUB of the same name beside it. `--report` reads the
book and writes nothing, so it works on EPUBs only; use `--dry-run` for anything else.

Every layout setting has a flag (`--no-covers`, `--min-width 70`, and so on) starting from the
value saved in calibre, and `--defaults` ignores the saved configuration so a script behaves the
same on every machine. Books are reported as they finish, prefixed `[n/total]`, and a book that
fails costs that book only. `--json` writes the full result including the ledger to stdout, with
diagnostics on stderr.

|code|meaning|
|---|---|
|0|nothing failed|
|1|at least one book failed|
|2|the command line was wrong|
|3|`--fail-on-change` was given and a book needs work|

Three behaviours differ from the buttons. Books are processed on a copy that replaces the original
only once every stage has succeeded, so an interrupted run leaves nothing half-written. A result is
kept when the fix changed something, when a conversion produced a book that did not exist before,
or when `--polish` or `--beautify` was named — not on byte difference alone, because polish rewrites
a book every time it runs and a scheduled job would otherwise churn the library and overwrite each
`ORIGINAL_EPUB` with the previous run's output. And polish runs without *Embed referenced fonts* or
*Download external resources*, which reach off the machine.

## Undo

Every modified book gets an `ORIGINAL_EPUB` format, created with the same calibre call the Polish
book action uses. calibre's own **Restore original** reverts the change. No `.bak` files are left
anywhere, except where you asked for them with the command line's `--backup`.

## Known limitations

- The fix does not survive a KFX round trip. KFX has no SVG page concept, so converting a fixed EPUB
  to KFX and back reintroduces the defect in a slightly different shape. Re-run the fix afterwards.
  A measured round trip also loses the page-list (492 entries in the book tested) and replaces the
  real ASIN.
- Captioned and multi-image pages are left alone unless you turn them on. Both are off by default
  because the layout is a guess: a caption gets a fixed 15% of the page and will clip if it needs
  more, and stacked images may not be what the original intended.
- Going via AZW3 (`kfx → azw3 → epub`) is worse than converting directly. It fragments chapters and
  loses the same metadata. Convert KFX → EPUB directly.
- The cover overrides are appended to the page's own stylesheet, so on a cover page that already
  styles `html, body` or `svg` calibre's *Check book* reports a duplicate selector. It renders
  correctly — the plugin's rules come last and are `!important` — and it only happens where another
  tool wrote those selectors first: one book in seventeen, in a library that had been through the
  PowerShell script this was ported from.

## Credits

Built with [Claude Code](https://claude.com/claude-code), using Claude Opus 5.

## Licence

GPL-3.0, see [LICENSE](LICENSE). calibre is GPL v3 and this plugin imports its modules.
