# Quality contract

SlideGuard accepts an export only when every applicable gate passes. `PASS_WITH_SOURCE_WARNINGS` means the artifact is published but the source contains an item that could not be proved safe. `FAIL` means no final package is published in strict mode.

| Risk | Source signal | Gate | Failure code |
|---|---|---|---|
| Wrong slide | selected slide number | one-page export plus distinct multi-slide fixture hashes | `STRUCTURE_PAGE_COUNT` |
| Dashed line becomes solid | OOXML `prstDash` | byte-identical PDF page operators | `FIDELITY_DASH` |
| Line, shadow, gradient or text changes | OOXML feature inventory | byte-identical PDF page operators | `STRUCTURE_CONTENT_STREAM` |
| SVG path/filter/dash changes | native SVG vector subtree | normalized SHA-256 invariant | `STRUCTURE_SVG_VECTOR_INVARIANT` |
| Whole page becomes a bitmap | PDF/SVG operator inventory | vector/text node required | `STRUCTURE_VECTOR_CONTENT` |
| Original image is softened | PPTX media stream | matched source image replaces downsampled stream | `IMAGE_UNMATCHED_CANDIDATE` |
| Alpha is lost | alpha-bearing source image | SVG mask count and PDF soft-mask update | `FIDELITY_ALPHA_MASK` |
| White SVG page appears | SVG root structure | no opaque page-sized white rectangle | `FIDELITY_ALPHA_CANVAS` |
| Transparent effects change on paper | PowerPoint white reference | SVG composited on white at several widths | `FIDELITY_SVG_WHITE_MATTE` |
| External fetch or script | SVG URI/event inventory | only `data:` and fragment references | `SEC_SVG_EXTERNAL_RESOURCE` |
| PDF zoom seam | raster comparison at several DPI values | residual row/column seam score under threshold | `FIDELITY_SEAM` |
| Crop/page size changes | accepted page box | all five PDF boxes match within 0.01 pt | `STRUCTURE_PAGE_BOX` |
| Source is modified | SHA-256 | before equals after | `SOURCE_IMMUTABLE` |
| PDF exceeds budget | candidate byte size | strict less-than limit | `SIZE_BUDGET_EXCEEDED` |
| A detected feature has no check | coverage map | validator required | `QA_COVERAGE_GAP` |

## Visual thresholds

PDF is rendered at 72, 96, 120, 144, 192, 300 and 600 DPI by default. Slides without pictures need SSIM ≥ 0.94 and normalized MAE ≤ 0.08 against the native PowerPoint PDF with the same crop. Slides with restored pictures use SSIM ≥ 0.85 because the restored source is intentionally sharper than PowerPoint's reduced image. The PDF operator hash remains exact in both cases.

The seam detector looks for a narrow error peak against neighboring rows and columns. Its current limit is 0.55. Fixture and fault tests must run when a threshold changes; threshold changes also require a pipeline revision update.

SVG is also composited onto white and compared with PowerPoint's white reference. The SSIM floor is 0.90 without pictures and 0.75 when original image streams were restored; MAE must stay at or below 0.12 and the seam limit stays 0.55.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | accepted |
| 10 | bad input |
| 20 | missing or incompatible environment |
| 30 | PowerPoint/export failure |
| 40 | size budget cannot be met |
| 50 | fidelity or security gate failed |
| 70 | unexpected internal error |
