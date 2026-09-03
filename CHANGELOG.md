# Changelog

## 0.1.0 - 2026-09-04

- Added read-only PowerPoint PDF/PNG broker with short-path handling.
- Added OOXML feature and embedded-media inventory.
- Added source-image restoration for PDF and SVG, including alpha masks and crop variants.
- Added transparent SVG canvas cleanup and tight cropping.
- Added strict PDF byte budget with candidate profiles.
- Added structure, security, multi-DPI, seam, alpha and coverage checks.
- Added JSON, HTML and JUnit reports plus package checksums.
- Added deterministic PowerPoint fixture generation and SVG fault injection.
- Fixed the `ppPrintCurrent`/`ppPrintSlideRange` multi-page export bug.
- Added manual percentage crop and per-edge percentage expansion shared by PDF, SVG and PNG.
- Added compact SVG output that changes embedded bitmap payloads without changing the vector/filter subtree.
- Added Chromium-first transparent SVG rendering for PowerPoint-compatible mask verification.
- Added long-path-safe atomic publication and package verification for deep OneDrive folders.
