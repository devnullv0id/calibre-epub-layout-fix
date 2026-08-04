# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Fix books automatically as they are added to the library**, off by default, configured under
  *Preferences → Plugins → Customize → Automatic*. It hooks calibre's database event stream rather
  than the *Add books* action, so drag and drop, the content server, a connected device and a
  watched folder all trigger it. Books arriving in another format are converted to EPUB and the
  format they arrived in is kept. Events are debounced so one import produces one batch and one
  summary. Three guards stop the plugin re-processing its own write-back: a suppression set, the
  `ORIGINAL_EPUB` backup and a per-session record of what has been queued.
- `alt` text is carried across as the rewritten page's accessible name (`role="img"` plus an SVG
  `<title>`), instead of being dropped.
- Navigation links pointing at pages that are not in the archive are neutralised — a dangling TOC
  entry becomes unlinked text, a dangling landmark is removed. calibre leaves one behind whenever
  it replaces the publisher's cover page.
- `python build.py --restart` closes calibre, builds, installs and starts calibre again.
  `--check-version` guards a release tag against a mismatched `PLUGIN_VERSION`.
- A GitHub Actions workflow that runs the engine suite, builds the zip on every push, and attaches
  it to the release for a `v*` tag.

### Fixed

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

[Unreleased]: https://github.com/devnullv0id/calibre-epub-layout-fix/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/devnullv0id/calibre-epub-layout-fix/releases/tag/v0.1.0
