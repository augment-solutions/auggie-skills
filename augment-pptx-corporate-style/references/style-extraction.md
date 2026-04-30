# Style extraction audit

Source file: `~/Downloads/Augment-FMD-Deck.pptx` (13.7 MB, 21 slides).

This document records exactly what was inspected to derive the tokens in `theme.js`. If the brand evolves, re-run the same probes against the new reference deck and diff against the values below.

## Methodology

1. Unpacked the OOXML to `/tmp/fmd-deck-unpacked` with `python -m zipfile -e`.
2. Parsed `ppt/theme/theme1.xml` for the declared color and font schemes.
3. Walked every `ppt/slides/slideN.xml` and counted:
   - `<a:latin typeface="...">` font face usage
   - `<a:rPr sz="N">` font sizes (half-points)
   - `<a:srgbClr val="HEX">` text/shape colors
   - `<a:buChar char="...">` and `<a:buAutoNum>` bullet styles
4. Inspected slide masters for default placeholder styles.
5. Listed `/ppt/fonts` to confirm the embedded typefaces.

## Findings

### Color scheme (declared)

`theme1.xml` declares "Simple Light" but the actual deck uses many overrides:

| Slot | Value |
|---|---|
| dk1 | `#000000` |
| lt1 | `#FFFFFF` |
| dk2 | `#F5F5F5` |
| lt2 | `#323232` |
| accent3 | `#99A2FF` |
| accent4 | `#DADDFF` |

### Color frequency in slides (top values)

| Hex | Uses | Role we assigned |
|---|---|---|
| `#008236` | 166 | Primary brand green |
| `#595959` | 139 | Body gray |
| `#8A8A8A` | 93 | Muted on dark |
| `#99A2FF` | 91 | Periwinkle accent |
| `#2E2E2E` | 91 | Strong body on light |
| `#F5F5F5` | 82 | Card/panel fill |
| `#FFFFFF` | 77 | Inverse text |
| `#D1D5DB` | 66 | Subtle dividers |
| `#B91C1C` | 15 | Risk/error callouts |

### Typography

Master placeholders default to Arial 14pt, but every slide overrides with embedded brand fonts:

| Typeface | Uses | Role |
|---|---|---|
| Inter | 339 | Body |
| DM Mono Medium | 235 | Eyebrows / metric labels |
| DM Mono | 140 | Code, captions |
| Inter Medium | 87 | Slide headings |
| Inter Light | 78 | Quiet labels |
| Urbanist | 46 | Big display titles (cover/section) |

`/ppt/fonts/` embeds Inter, InterMedium, InterLight, DMMono, DMMonoMedium, Urbanist (with bold/italic variants), plus HelveticaNeue and Roboto as fallbacks.

### Font sizes

| Pt | Frequency | Use we mapped |
|---|---|---|
| 36 | cover slides ("Your AI for", "Thank you", "Appendix") | `SIZES.cover` |
| 25-29 | section headers | `SIZES.sectionTitle` |
| 20 | slide titles (dense slides) | `SIZES.slideTitle` |
| 12-14 | body paragraphs | `SIZES.body / bodyLg` |
| 7-10 | captions, micro labels | `SIZES.caption / micro` |

### Bullets

| Glyph | Uses |
|---|---|
| `➔` (U+2794) | 43 |
| `→` (U+2192) | 18 |
| `●` (U+25CF) | 2 |
| `■` (U+25A0) | 2 |

`<a:buNone>` appears 505 times — most paragraphs are unbulleted with a glyph used only at top-level list items.

### Layout & motifs

- All four slide masters use a `dk1` (black) base with full-cover rectangles for branding overlays.
- Recurring full-bleed `#008236` rectangles serve as accent stripes / hairlines, not as full slide backgrounds.
- A small green hairline above section titles is consistent across the deck — codified as the `addSectionSlide` accent shape.
- Slide footers commonly carry a "Augment Code" wordmark in DM Mono ~8pt, mid-gray.

## What we deliberately omitted

- **Logo images.** The reference deck embeds many logo lockups (`image4.png`, `image40.png`, `image41.png`, etc.). We do not bundle these in the skill — pass an explicit path to `slide.addImage` if you want a logo on cover slides.
- **Charts.** No theme-level chart styling is shipped; the deck's charts are built ad-hoc on each slide. Use `pptxgenjs.addChart` directly with `COLORS.green`, `COLORS.periwinkle`, and `COLORS.bodyGray` as the series colors.
- **Animations / transitions.** pptxgenjs does not support these in any meaningful way; the reference deck doesn't use them either.
