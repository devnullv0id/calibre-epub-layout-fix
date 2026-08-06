# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.2.0] - 2026-08-06

### Added

- **A command line**, through calibre's plugin CLI hook:
  `calibre-debug -r "EPUB Layout Fix" -- --help`. Runs the same pipeline as the toolbar buttons,
  on files and directories or on library books picked with `--search`, `--ids` or `--all`.
  `--report` and `--dry-run` write nothing. `--convert`, `--output-dir` and `--backup` cover the
  file cases. Every layout setting has a flag; `--defaults` ignores the saved configuration
  entirely. `--json` gives the full result including the ledger, and the exit status separates a
  failed book (1), a bad command line (2) and, with `--fail-on-change`, books that need work (3).

  Progress is printed per book as it finishes, and a book that fails costs that book rather than
  the run. Repeated runs are a no-op: a result is kept only when the fix changed something, a
  conversion produced a new book, or `--polish`/`--beautify` was named, so a scheduled run cannot
  churn a library or overwrite each book's `ORIGINAL_EPUB` with the previous run's output. Polish
  runs without *Embed referenced fonts* and *Download external resources*, which reach off the
  machine. See the README for the rest.

- `tests/test_cli.py`, covering the above against real books and a throwaway library.

- **A dry run.** Runs the whole pipeline on a temporary copy and discards the result instead of
  saving it: nothing in the library changes and no `ORIGINAL_EPUB` backup is made. Unlike the
  report action it says what a real run would *produce*, since polish, the EPUB 3 upgrade and
  beautify all happen first. Available as a setting, and as *Fix layout (dry run)* in the menu for
  a one-off that leaves the setting alone. It says so everywhere it could be mistaken for a real
  run: the job name, every stage in the Status column (*Dry run: polishing*, not *Polishing*), the
  finished state (*Dry run - discarded*, not *Finished*) and the summary. A setting left on cannot
  quietly swallow a run.
- **Books that need work are flagged in the library** with a red pin, using calibre's own
  marked-books mechanism. `marked:needs-fix` lists them. A report or a dry run sets the pin; a
  real run takes it off once the book is fixed. Marks made by other plugins survive, and
  re-running refreshes only our own label rather than accumulating stale pins. The colour is
  normally chosen at random per label from calibre's palette, so the model's icon cache is seeded
  with `render_pin('red')` first.
- **A report action.** *EPUB Layout Fix - report* runs the full detection and writes nothing, then
  shows one row per book expanding to one row per page examined: action, category, image size and
  the reason it was chosen. Exports to CSV. The engine could always plan a run without performing
  it (`analyze_epub`) and has always recorded a ledger entry for every page it looked at; neither
  had a way in from the interface until now.
- **Captioned pages and multi-image pages can now be fixed**, both off by default. A page with one
  image and a caption of up to 120 characters keeps the caption below the image, with its own
  markup rather than flattened to text, and gives it 15% of the page height. A page with several
  images and no text stacks them, sharing the height equally, and is skipped whole if any single
  image fails the width threshold. Both were previously detected and then always left alone.
- **Beautify all files**, off by default, in the Polish panel. Runs calibre's own
  `pretty_all` — the editor's *Tools → Beautify all files* — over every content document,
  stylesheet and the OPF, last before the layout fixes so the generated pages keep their own
  formatting. It is whitespace-safe by construction, but it changes nothing about the rendered
  book and rewrites every file, so a book that needed no repair still comes out changed; hence
  the default.

- **The bulk conversion window when more than one book is selected.** *Convert to EPUB and fix…*
  always opened calibre's single-book window, built for the first selected book, and then applied
  those settings to all of them — including Metadata and a Search & replace written against one
  book's text. It now picks the window the way calibre does: one book gets *Convert*, several get
  *Bulk convert N books*, which drops the categories that cannot be shared and adds **Use saved
  conversion settings for individual books**. That checkbox is honoured, layered the way calibre's
  own bulk convert layers it — the input format's bulk defaults, then the book's saved settings,
  then the window's settings on top — but the plugin never writes those saved settings back.

- **Fix books automatically as they are added to the library**, off by default, configured under
  *Preferences → Plugins → Customize → Automatic*. It hooks calibre's database event stream rather
  than the *Add books* action, so drag and drop, the content server, a connected device and a
  watched folder all trigger it. Books arriving in another format are converted to EPUB and the
  format they arrived in is kept. Events are debounced so one import produces one batch and one
  summary. Three guards stop the plugin re-processing its own write-back: a suppression set, the
  `ORIGINAL_EPUB` backup and a per-session record of what has been queued.
- `alt` text is carried across as the rewritten page's accessible name (`role="img"` plus an SVG
  `<title>`), instead of being dropped.
- References to files that are not in the archive are neutralised, across every content document
  rather than only the navigation one — a dangling TOC entry becomes unlinked text, a dangling
  landmark is removed, and a `<link rel="stylesheet">` pointing at a stylesheet calibre trimmed is
  dropped. calibre leaves a dead cover link behind whenever it replaces the publisher's cover page,
  and a dead `page_styles1.css` link in some books.
- `python build.py --restart` closes calibre, builds, installs and starts calibre again.
  `--check-version` guards a release tag against a mismatched `PLUGIN_VERSION`.
- A GitHub Actions workflow that runs the engine suite and builds the zip on every push. Releases
  follow `PLUGIN_VERSION`: a version with no tag yet gets one, with the zip attached. Bumping the
  version in `action_base.py` is the release action, so a tag can never name a version the zip
  disagrees with, and GitHub's own `/releases/latest` stays pointed at the newest one.

### Fixed

- The red pin never appeared in the row margin. calibre's icon cache maps a label to a
  `(colour, QIcon)` pair; a bare icon there made `marked_text_icon_for` raise inside `headerData`,
  which cost every row its pin while leaving the marks intact, so `marked:needs-fix` found the
  books and nothing showed. The test fake had no such attribute, so the failed seeding was
  swallowed and the suite stayed green. It now models the real shape.

- **The library's metadata reached the converted book.** The conversion wrapper passed no metadata,
  so the output kept whatever the source file had embedded: authors appeared in sort form and, on
  multi-author books, in the wrong order; every `file-as` refinement read `Unknown`; and the
  `calibre:<uuid>` identifier that ties a file to its library record was replaced by a fresh one.
  The library metadata is now written to a temporary OPF and passed as `read_metadata_from_opf`,
  the way calibre's own *Convert* action does. The cover is stripped from it deliberately, so the
  publisher's own cover page and full-resolution cover image survive.
- **The cover page was repaired by a weaker path than every other page.** It was patched in place
  while keeping its stylesheet links and `class` attributes, so a rule like
  `.calibre2 { height: auto; width: auto }` outranked its `height="100%"` presentation attribute
  and left the cover at the reader's default size — the defect the plugin exists to repair. It now
  gets explicit full-bleed overrides, or the full rewrite when the page qualifies for it.
- The injected cover block is delimited at both ends, so re-running replaces it whole. Previously a
  second run rewrote *every* `background-color` in the document.
- A batch could stall short of its report: completions were counted by collected results, so a job
  returning nothing meant the summary never appeared.
- The library view is refreshed after the fixed book is written back, so the format size updates
  without waiting for something else to refresh it.
- Temporary files are removed for skipped and failed books too, not only committed ones.

- **`tests/test_matrix.py`**, a wide end-to-end sweep. It crosses ten settings combinations with
  every fixture and any books passed on the command line, and after each run asserts the
  invariants that must hold whatever is switched on: the archive is a valid EPUB with `mimetype`
  first and stored and every CRC intact, every XHTML and the OPF still parse, no entry is gained
  or lost, anything not meant to be touched is byte-identical, nothing references a file that is
  not in the archive, `analyze_epub` agrees with `fix_epub` and writes nothing, and a second run
  changes nothing. Under `calibre-debug` it also crosses the target version, polish, beautify and
  conversion stages, and checks every setting round-trips from `config` into the engine.
- `tests/test_library.py` now drives the toolbar action's own path against a real library — job
  spec, worker, write-back, backup, view refresh and temporary-file cleanup — with a stubbed main
  window.

### Changed

- Filtering the library down to the flagged books is now offered rather than done: the question
  comes after the summary, defaults to no, and carries calibre's *Ask this again* checkbox.

### Removed

- `jobs.run_batch`, unused since jobs became one per book, and the `write_audit_csv` /
  `audit_csv_path` preferences, which were never read.

## [0.1.0] - 2026-08-04

First working version.

### Added

- **Layout fixing engine** (`fixer.py`), free of calibre and Qt imports so it can be tested
  headlessly. Rewrites full-page images as SVG page objects, repairs SVG pages carrying
  `preserveAspectRatio="none"`, and paints dark letterbox bands around covers.
  - Detection resolves the real CSS cascade rather than matching class names, so it works on books
    from any producer. Percentage widths compound down the ancestor chain, which keeps deliberate
    ornaments from being mistaken for full-page art.
  - Image dimensions read from JPEG, PNG, GIF, WebP, BMP and SVG headers.
  - `properties="svg"` added to the OPF manifest for EPUB 3 only, matched on the resolved href so
    percent-encoded and `./`-prefixed entries still match.
  - Anchor ids preserved on rewritten pages so table-of-contents and page-list links keep working.
  - Archives rebuilt with `mimetype` first and stored, and verified before anything is replaced.
  - Every image-bearing page that is not rewritten is recorded with a reason.
- **Three calibre actions**, each placeable independently in Preferences → Toolbars & menus:
  *Fix layout…*, *Fix layout (last settings)* and *Convert to EPUB and fix…*.
- **Layout fixes and Polish panels** that slot into calibre's own conversion window rather than
  imitating it. The Polish panel is built from calibre's operation list at runtime.
- **Target EPUB version**, defaulting to EPUB 3, which upgrades book internals using calibre's own
  step before the layout fixes are applied.
- Background execution as a normal calibre job, and an `ORIGINAL_EPUB` backup so calibre's own
  *Restore original* reverts a run.
- Test suites: engine fixtures and parity, an offscreen Qt smoke test, and an end-to-end pipeline
  test.

[Unreleased]: https://github.com/devnullv0id/calibre-epub-layout-fix/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/devnullv0id/calibre-epub-layout-fix/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devnullv0id/calibre-epub-layout-fix/releases/tag/v0.1.0
