# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/devnullv0id/EPUB-Layout-Fix/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/devnullv0id/EPUB-Layout-Fix/releases/tag/v0.1.0
