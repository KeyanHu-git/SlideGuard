# Architecture

The optional Qt Quick Studio adapter is specified in [studio-architecture.md](studio-architecture.md).
It separates the editing model, display renderer and asynchronous application adapter.
The Office rendering truth and independent delivery validators below remain unchanged.

## Trust model

SlideGuard uses three kinds of truth because none is enough on its own.

1. The PPTX package is authoring truth. OOXML gives slide order, source media, crop values, relationships and the presence of features such as dashes or shadows.
2. Desktop PowerPoint on the current machine is rendering truth. It resolves Office themes, fonts, SmartArt and PowerPoint-only effects better than a third-party reimplementation.
3. The exported artifact is delivery truth. PDF and SVG parsers, rasterizers and checksums test what the reader will receive.

The tool never rebuilds a slide from OOXML coordinates. That route looks attractive but changes too many details: font metrics, dash phase, shadow blur, clipping, connector endpoints and group transforms.

## Pipeline

```text
PPTX (read-only)
  │
  ├── OOXML inventory ────────────────┐
  │                                   │
  └── PowerPoint broker               │
       ├── native one-page PDF        │
       └── high-width reference PNG   │
                │                     │
                ├── PDF image restore │
                │    └── shared crop  │
                └── PDF → SVG         │
                     ├── image restore│
                     └── remove only the synthetic white page
                                      │
                          validators + evidence
                                      │
                              atomic publish
```

PowerPoint COM writes to a short ASCII temporary path first. Some Office builds reject long or non-ASCII output paths even when Windows and Python accept them. After PowerPoint closes the file, SlideGuard copies it into the isolated job directory.

## State machine

`DISCOVER → PREFLIGHT → INVENTORY → NATIVE_EXPORT → PATCH → VALIDATE → PACKAGE → PUBLISH`

Any failed state stops publication. A failed job stays in `%LOCALAPPDATA%\SlideGuard\w` with its report and evidence so the cause can be reproduced. A successful job is copied to a hidden publish directory and renamed atomically to its final job ID.

## Invariants

- Input file hash before and after the run must match.
- Native PDF page count must be one per selected slide.
- The PDF content stream hash must not change during image replacement or cropping.
- All PDF page boundary boxes must share the accepted crop.
- SVG must parse with network and entity resolution disabled.
- SVG links must be fragment IDs or embedded `data:` resources; scripts and event handlers fail.
- The artificial page-sized white rectangle may be removed. Slide-authored white objects may not.
- Every detected feature needs a successful validator. Missing coverage becomes `QA_COVERAGE_GAP`.
- The output size uses a strict `< limit` comparison.

## Image restoration

PowerPoint often downsamples a picture when it writes PDF. SlideGuard extracts candidate media from the PPTX ZIP, applies the authoring crop, compares premultiplied low-resolution signatures, then replaces only the matched PDF/SVG image stream. Geometry and draw order do not change.

An alpha PNG normally becomes an RGB image plus a grayscale mask in PDF/SVG. SlideGuard updates both streams. RGB pixels hidden under alpha are allowed a lower matching threshold because PowerPoint preserves arbitrary hidden RGB values there. The accepted threshold is fixed in source and covered by a fixture with two crops of the same PNG.

## Reproducibility

The job ID includes the source SHA-256, normalized options and pipeline revision. Reports record tool version, Office version, executable paths, package hashes and every threshold. A rerun with the same inputs produces the same logical job ID; content hashes show any renderer drift.
