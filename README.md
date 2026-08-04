# EPUB Layout Fix

A calibre plugin that repairs full-page images and covers in EPUB files so they display correctly
on e-readers — particularly Kobo, Tolino and PocketBook, which use the Adobe RMSDK renderer.

> **Status: in development.** The engine is a port of a PowerShell script that has been run across a
> 996-book library (1,446 image pages rewritten, 973 covers repaired, 0 failures). This README is
> filled in as the plugin takes shape.

## What it fixes

**Full-page images clipped at the edge or spilling off the page.** A page whose image sits inside a
box at `width: 100%` that *also* carries side margins measures wider than the reader's column, so
the reader clips it:

```css
body { width: 100%; margin: 0 5pt; }   /* 100% + 10pt = wider than the column */
img  { width: 100%; }
```

The same page can also be too *tall*: an image that fits horizontally still overruns the bottom of
the page when its aspect ratio is narrower than the screen's.

**Stretched covers.** calibre writes `preserveAspectRatio="none"` into its generated title page
unless *Preserve cover aspect ratio* is enabled, which distorts every cover.

**Bright letterbox bands** around a correctly-fitted cover.

## How

Qualifying pages are rewritten as a self-contained SVG page object, so the SVG renderer fits the
image in both dimensions rather than the CSS cascade doing it:

```xhtml
<svg width="100%" height="100%" viewBox="0 0 1200 1729" preserveAspectRatio="xMidYMid meet">
  <image width="1200" height="1729" xlink:href="map.jpg"/>
</svg>
```

Detection is **structural, never based on class names** — the plugin resolves the real CSS cascade
to work out how wide an image is actually displayed, so it works regardless of which tool produced
the book.

## Requirements

- calibre 5.0 or newer (developed against 9.12.0)
- The KFX Input plugin, only if you want to convert from KFX

## Installation

*(to be completed — build with `python build.py`, then load `dist/EPUB-Layout-Fix.zip` via
Preferences → Plugins → Load plugin from file)*

## Licence

GPL-3.0 — see [LICENSE](LICENSE). calibre is GPL v3 and this plugin imports its modules.
