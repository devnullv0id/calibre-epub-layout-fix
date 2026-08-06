# EPUB Layout Fix

A calibre plugin that repairs full-page images and covers in EPUB files so they display correctly
on e-readers. Mainly aimed at Kobo, Tolino and PocketBook, which use the Adobe RMSDK renderer.

Select books, click a button, and the work runs in the background as a normal calibre job.

---

## What it fixes

### Full-page images clipped at the edge

A page whose image sits inside a box at `width: 100%` that also has side margins measures wider
than the reader's column, so the reader clips the overflow:

```css
body { width: 100%; margin: 0 5pt; }   /* 100% + 10pt = wider than the column */
img  { width: 100%; }
```

This is a collision rather than a single bug. The `width: 100%` comes from the book, often from the
KFX Input plugin translating Amazon's layout. The `5pt` margins come from calibre's own Page setup
conversion settings. Neither is wrong on its own.

### Full-page images that overrun the bottom of the page

An image can fit horizontally and still be too tall. A 1200×1729 map at `width: 100%` on a Kobo
Clara renders about 1545px tall in roughly 1448px of usable height. That is about 7% too tall, so it
clips or spills onto a second page.

### Stretched covers

calibre writes `preserveAspectRatio="none"` into its generated title page unless *Preserve cover
aspect ratio* is enabled, which distorts every cover it produces.

### Bright letterbox bands

Once a cover is correctly fitted, the leftover bands take the page background, which is white by
default.

---

## How it fixes them

Qualifying pages are rewritten as a self-contained SVG page object, so the SVG renderer fits the
image in both dimensions instead of the CSS cascade:

```xhtml
<div class="fullpage"><svg width="100%" height="100%" viewBox="0 0 1200 1729"
     preserveAspectRatio="xMidYMid meet">
  <image width="1200" height="1729" xlink:href="map.jpg"/>
</svg></div>
```

Three things matter here, all found by testing on a real Kobo:

- No `width: 100%` anywhere. These are block boxes, so auto width already fills the column. Forcing
  `100%` adds the reader's own injected margins on top and pushes the image off-centre.
- No `vh` units. Adobe RMSDK ignores them silently, so a `max-height: 95vh` cap does nothing on
  exactly the readers that need it most.
- The book's stylesheets are not linked on the rewritten page, so nothing in the book can override
  the result.

Existing SVG pages that only carry `preserveAspectRatio="none"` are repaired in place rather than
rewritten. That includes covers.

---

## Why a plugin rather than just settings

Two of the four causes are fixable with calibre settings, and you should set them anyway:

| Problem | Fixable by a setting? |
|---|---|
| Stretched covers | Yes. Preferences → Conversion → Output options → EPUB output → *Preserve cover aspect ratio* |
| Horizontal clipping | Yes. Preferences → Conversion → Common options → Page setup → set left/right margins to 0 |
| Vertical fit | No. Needs the image fitted in both dimensions; `max-height` needs a definite ancestor height and `vh` is ignored by RMSDK |
| Dark letterbox | No. Hardcoded in calibre's cover template, and Extra CSS never reaches the generated title page |

The plugin also repairs books already on disk, which no forward-looking setting can do.

---

## Requirements

- calibre 5.0 or newer (developed against 9.12.0)
- The [KFX Input](https://www.mobileread.com/forums/showthread.php?t=291290) plugin, only if you
  want to convert from KFX

## Installation

Build the zip, then load it:

```
python build.py
```

In calibre: **Preferences → Plugins → Load plugin from file** → `dist/EPUB-Layout-Fix.zip`.

Or from the command line:

```
calibre-customize -a dist/EPUB-Layout-Fix.zip
```

Restart calibre, then place the buttons via **Preferences → Toolbars & menus**.

While developing, one command does the whole loop. calibre gets closed first because it writes its
stale in-memory plugin list back on exit, which silently undoes an install made while it was open:

```
python build.py --restart
```

Every push also builds the zip in CI. The artifact is attached to each run, and to the release for a
`v*` tag.

## Usage

Four actions are contributed, each placeable independently:

| Action | What it does |
|---|---|
| **EPUB Layout Fix** | Opens the settings window, then repairs the selected books' existing EPUB |
| **EPUB Layout Fix - quick run** | Repairs immediately with the stored settings, no dialog |
| **EPUB Layout Fix - convert and fix** | calibre's own conversion window with **Polish** and **Layout fixes** panels added; converts, polishes, then repairs |
| **EPUB Layout Fix - report** | Lists what would be changed, page by page, and writes nothing |

The conversion window is calibre's real one, with Metadata, Look & feel, Page setup and the rest
unchanged, plus two extra categories in the left-hand list.

Selecting more than one book opens calibre's *Bulk convert N books* window instead, the same way
calibre's own Convert action chooses between them. That window drops the categories that only make
sense for a single book (Metadata, Debug, the input format) and adds **Use saved conversion settings
for individual books**. With that ticked, each book's own saved settings are used for anything the
bulk window cannot specify, and the window's settings are layered on top. The plugin reads those
saved settings but never writes them back.

### Fixing books automatically as they arrive

**Preferences → Plugins → Customize → Automatic** turns on a listener that repairs books as they are
added to the library. It is off by default. Before turning it on:

- It hooks calibre's database event stream rather than the *Add books* action, so every route in
  counts: Add books, drag and drop, the content server, a connected device, a watched folder.
- A book that arrives in another format is converted to EPUB and then repaired. The format it
  arrived in is never removed.
- Events are debounced, so adding fifty books produces one run and one summary at the end rather
  than fifty dialogs.
- A book is never processed twice. An existing `ORIGINAL_EPUB` backup means it has been done
  already, and the plugin's own write-back is suppressed rather than treated as a new import.
- *Embed referenced fonts* and *Download external resources* are forced off for automatic runs
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
| Dark letterbox bands | on | Paint the bands around the cover |
| Letterbox colour | `#000000` | Any hex colour |
| Target EPUB version | EPUB 3 | EPUB 3 upgrades book internals first; EPUB 2 leaves the version alone |
| Polish | calibre's own settings | calibre's polish operations, run before the layout fixes |
| Beautify all files | off | Pretty-print the book's source, cosmetic only, see below |
| Dry run | off | Do all the work, then discard the result instead of saving it |
| Fix books automatically | off | Repair books as they are added to the library |
| Convert other formats | on | Automatic runs convert non-EPUB books, keeping the original format |
| Wait for the import to finish | 3 s | How long to collect an import before starting one batch |
| Ask before processing more than | 100 | Confirm once before queueing a very large import |

The Polish panel is built from calibre's operation list at runtime, so it always matches the Polish
book dialog instead of drifting from a hardcoded copy.

### Beautify all files

Runs calibre's own *Beautify all files*, the one in the editor's Tools menu, over every content
document, stylesheet and the OPF. It cannot change rendering: calibre re-indents an element only
when every one of its children is a block-level tag with a whitespace-only tail, so it never injects
whitespace into a run of inline elements where it would show up as a visible space.

Off by default. Leave it off unless you edit books by hand:

- it makes no difference to the rendered book, it is for reading the source in the editor;
- it rewrites every file, so a book that needed no repair still comes out changed and still gets an
  `ORIGINAL_EPUB` backup.

It runs last before the layout fixes, so the pages the plugin generates keep their own formatting.

### Metadata and the cover

When the plugin converts, it hands calibre the book's library metadata the same way calibre's own
Convert action does. The author's display name, the author order, the sort names and the
`calibre:<uuid>` identifier all survive into the EPUB. Without that step the output keeps whatever
the source file had embedded, which is how books end up shelved under "Rowling, J.K." with a sort
name of "Unknown".

The library cover is not passed to the conversion. Supplying one makes calibre replace the
publisher's cover page with a regenerated title page at a lower resolution, and it then leaves the
navigation document pointing at the page it just deleted, so "Cover" becomes a dead entry in the
table of contents. Keeping the original page avoids both.

Books that already have that defect, from a conversion done outside the plugin, get those links
repaired on the next run. Same for a `<link rel="stylesheet">` pointing at a stylesheet calibre
trimmed: it is removed, since a reader cannot load it either way.

## How detection works

Detection is structural and never looks at class names, which is what makes it work on books from
any producer. For each candidate page the plugin resolves the real CSS cascade (linked stylesheets,
`<style>` blocks and inline attributes, with specificity) to work out how wide the image is actually
displayed.

Percentage widths compound down the ancestor chain. A `body` at `39.961%` holding an `img` at `100%`
displays that image at about 40% of the column, not 100%. Testing only the image's own width would
mistake deliberate ornaments for full-page art.

A page qualifies when its `<body>` contains no text and exactly one `<img>`, it is not already
SVG-wrapped, and the effective width is at least the configured threshold.

Every image-bearing page that is not rewritten is recorded with a reason:

| Category | Meaning |
|---|---|
| `has-text` | Ordinary prose with an inline image |
| `captioned-candidate` | One image plus a short caption. Reported, not converted, so the text survives |
| `multi-image` | More than one image; rewriting would drop all but one |
| `too-narrow` | Displayed below the threshold, so probably a deliberate ornament |
| `unreadable-image` | Image format whose dimensions could not be read |
| `already-svg-ok` | Already a correct SVG page object |

### Dry run

**Dry run** in the Layout fixes panel does the entire job - convert, polish, upgrade, beautify,
repair, verify - on a temporary copy, and then throws the result away instead of writing it back.
Your library is untouched and no `ORIGINAL_EPUB` backup is made.

It says so everywhere it could otherwise be mistaken for a real run: the job is named
*Dry run: fix layout ...*, each stage in the Status column reads *Dry run: polishing* rather than
*Polishing*, a finished job says *Dry run - discarded* instead of *Finished*, and the summary opens
with `DRY RUN - nothing was written`. A sticky setting that silently swallowed your changes would
be a nasty surprise.
There is also **Fix layout (dry run)** in the main action's menu, which does one dry run without
touching the stored setting.

This is not the same as the report action below. A dry run answers "what would a real run
produce?", including everything polish and the EPUB 3 upgrade change on the way. The report only
looks at the EPUB as it is now.

### Reporting without changing anything

**EPUB Layout Fix - report** runs the same detection as a real run and writes nothing. It opens a
window with one row per book, expanding to one row per page examined: what was done or skipped, the
category, the image size and the reason. **Export CSV** writes the lot to a file.

Useful for answering "which of these 900 books actually need work?" before letting the plugin near
them, and for checking why a page you expected to be fixed was not.

### Flagging the books that need work

A report or a dry run puts a **red pin** against every book that would be changed, using calibre's
own marked-books mechanism, the same one Extract ISBN uses. The pin shows in the row margin and
`marked:needs-fix` in the search bar lists only those books.

The pin comes off by itself once a real run has fixed the book. Marks made by anything else are
left alone, and re-running does not leave stale pins behind: only our own label is refreshed.

## Command line

calibre passes a plugin the command line through `calibre-debug -r`, so no GUI is involved:

```
calibre-debug -r "EPUB Layout Fix" -- --help
```

It runs the same pipeline the toolbar buttons run, so a book repaired from a script and one
repaired from the button come out identical.

Files, or directories to scan:

```
# what would change, without touching anything
calibre-debug -r "EPUB Layout Fix" -- --report -v book.epub

# repair a tree of books in place, keeping each original as .bak
calibre-debug -r "EPUB Layout Fix" -- --recursive --backup ~/Books

# convert and repair, leaving the sources alone
calibre-debug -r "EPUB Layout Fix" -- --convert --output-dir out/ *.azw3
```

A directory gives up its EPUBs. With `--convert` it also gives up the formats the plugin converts
from — KFX, AZW3, MOBI, AZW, KEPUB, DOCX, FB2, PDF — except where an EPUB of the same name is
sitting beside them, since converting `book.azw3` over the `book.epub` next to it is never what
was meant. A file named directly on the command line is always taken as given, whatever its
extension.

`--report` only examines EPUBs: it reads the book and writes nothing, so there is no converted
file for it to look at. Use `--dry-run` for a non-EPUB — it converts into a temporary copy, runs
the whole pipeline, and keeps none of it.

Or books in a library, selected by search, by id, or all of them. Results are written back with
the usual `ORIGINAL_EPUB` backup, so **Restore original** still works. **Close calibre first** —
it holds the library in memory, so it will not see the changes and may write over them:

```
calibre-debug -r "EPUB Layout Fix" -- --library ~/Calibre --search "formats:EPUB" --report
calibre-debug -r "EPUB Layout Fix" -- --library ~/Calibre --ids 41,42
```

Every layout setting can be overridden per run — `--no-covers`, `--captioned`, `--min-width 70`
and so on — and each defaults to whatever is saved in calibre. `--defaults` ignores the saved
configuration entirely, which is what you want in a script that has to behave the same on every
machine.

Each book is reported as it finishes, prefixed `[n/total]`, so a long run shows where it is. One
book failing costs that book and nothing else: the run carries on and the summary counts it.

`--json` prints the full result of each book, ledger included, on stdout with the diagnostics kept
on stderr. Exit status is 0 when nothing failed, 1 when a book failed, 2 for a bad command line,
and 3 when `--fail-on-change` was given and a book needs work — enough for a CI job that fails when
a title lands in the library unrepaired.

Differences from the buttons, all deliberate:

- Books are processed on a copy and only moved over the original once every stage has succeeded,
  so an interrupted run cannot leave a half-polished file behind.
- A result is kept when the layout fix changed something, when a conversion produced a book that
  did not exist before, or when a rewriting stage was asked for by name (`--polish`,
  `--beautify`). Not merely because a stage touched the bytes: polish rewrites a book every time
  it runs, so keying off that would rewrite every book on every pass — and in a library, replace
  each book's `ORIGINAL_EPUB` with the previous run's output until the real original was gone.
  Running twice over the same books now changes nothing the second time.
- `ORIGINAL_EPUB` is only written when there isn't one already. calibre overwrites it, which is
  right for a one-off polish and wrong for a command that may be run on a schedule.
- Polish runs without *Embed referenced fonts* and *Download external resources*, whatever the
  conversion window has saved. The first copies fonts off this machine into the book, the second
  fetches remote URLs, and neither belongs in something running unattended — the same reasoning
  that already excludes them from automatic runs on import.

## Undo

Every modified book gets an `ORIGINAL_EPUB` format, created with the same calibre call the Polish
book action uses. calibre's own **Restore original** menu entry reverts the change. No `.bak` files
are left anywhere.

## Known limitations

- The fix does not survive a KFX round trip. KFX has no SVG page concept, so converting a fixed EPUB
  to KFX and back reintroduces the defect in a slightly different shape. Re-run the fix afterwards.
  A measured round trip also loses the page-list (492 entries in the book tested) and replaces the
  real ASIN.
- Captioned and multi-image pages are left alone unless you turn them on. Both are off by
  default because the layout is a guess: a caption gets a fixed 15% of the page and will clip if
  it needs more, and stacked images may not be what the original intended.
- Going via AZW3 (`kfx → azw3 → epub`) is worse than converting directly. It fragments chapters and
  loses the same metadata. Convert KFX → EPUB directly.

## Building and testing

```
python build.py                                   # -> dist/EPUB-Layout-Fix.zip
python build.py --install                         # also runs calibre-customize -a
python build.py --restart                         # close calibre, build, install, start it again

python tests/test_fixer.py [reference-library]    # engine: fixtures, parity, idempotency
python tests/test_matrix.py [book ...]            # every setting x every book, engine only
calibre-debug tests/test_matrix.py [book ...]     # ... plus the pipeline and settings plumbing
calibre-debug tests/smoke_gui.py                  # Qt widgets, offscreen
calibre-debug tests/test_pipeline.py [book ...]   # convert -> polish -> upgrade -> fix
calibre-debug tests/test_library.py               # throwaway library, action path, import listener
calibre-debug tests/test_progress.py              # job stages
calibre-debug tests/test_cli.py                   # the command line, end to end
```

`test_matrix.py` is the broad one. It crosses ten settings combinations with every fixture and any
books you hand it (or a folder in `EPLF_BOOKS`), and after each run checks the things that have to
hold whatever is switched on: the archive is a valid EPUB with every CRC intact, every XHTML and the
OPF still parse, no entry is gained or lost, anything not meant to be touched is byte-identical,
nothing references a file that is not in the archive, and a second run changes nothing. Under
`calibre-debug` it also crosses the target version, polish, beautify and conversion stages, and
checks that each setting read back through `config` is the one the engine receives.

The suites that drive calibre load the installed plugin, not the working tree. Run
`python build.py --install` before them or you will be testing the last build.

The engine imports nothing from calibre or Qt, so `test_fixer.py` runs under a plain interpreter.
Its parity test checks for identical results to the PowerShell implementation this was ported from,
across the same 21-book library.

## Credits

The detection rules come from measuring a 996-book library rather than guesswork, including the 80%
threshold, which separates genuine full-page art (90–100%) from deliberate ornaments (around 40%).

## Licence

GPL-3.0, see [LICENSE](LICENSE). calibre is GPL v3 and this plugin imports its modules.
