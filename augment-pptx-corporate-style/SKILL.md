---
name: augment-pptx-corporate-style
description: Build PowerPoint decks that match the Augment Code corporate brand. Extends the base `pptx` skill with a typed theme (colors, fonts, sizes, bullets) and high-level pptxgenjs helpers (`addCoverSlide`, `addSectionSlide`, `addContentSlide`, `addStatSlide`, `addCompareSlide`). Use when a user asks to create a slide deck "in Augment style", "branded", "corporate-looking", or asks for a deck and you want it to match the Augment FMD reference. Triggers on "build a deck", "make slides", "presentation in Augment style", "branded pptx", "corporate slides".
---

# augment-pptx-corporate-style

A thin theme layer on top of the base `pptx` skill. It does NOT replace `pptxgenjs` — it just supplies brand tokens and slide builders so generated decks look like the reference deck (`Augment-FMD-Deck.pptx`).

## When to use

Use this skill any time the user asks for an Augment-branded `.pptx`. If the request is unrelated to Augment's corporate identity (e.g. "make a generic blue deck"), prefer the base `pptx` skill directly.

This skill is purely additive. You can still call any `pptxgenjs` API (`slide.addImage`, `slide.addChart`, `slide.addTable`, etc.) on the slide objects returned by the helpers.

## Setup

The skill ships with a runtime helper module. From any working directory:

```bash
# install pptxgenjs once (globally is fine)
npm install -g pptxgenjs

# point Node at the global modules
export NODE_PATH="$(npm root -g)"
```

Then in your generator script:

```js
const PptxGenJS = require("pptxgenjs");
const augment = require(
  require("os").homedir() + "/.augment/skills/augment-slides-skill"
);

const pptx = augment.applyTheme(new PptxGenJS(), {
  defaultTheme: "light",     // "light" or "dark"
  title: "My Deck",
  author: "Augment Code",
});

augment.addCoverSlide(pptx, {
  eyebrow: "Q4 Review",
  title: "The Software Agent Company",
  subtitle: "Where the work happens.",
});

augment.addContentSlide(pptx, {
  title: "Why now",
  bullets: [
    "Code volume is exploding",
    { text: "Review capacity is flat", level: 0 },
    { text: "Quality regressions slip in", level: 1 },
  ],
});

await pptx.writeFile({ fileName: "deck.pptx" });
```

## Theme tokens

All tokens are exported from `index.js`:

| Token | Description |
|---|---|
| `COLORS.green` | `#008236` Augment Green - primary accent |
| `COLORS.periwinkle` | `#99A2FF` secondary accent |
| `COLORS.black` / `white` | dark/light master backgrounds |
| `COLORS.offWhite` | `#F5F5F5` card/panel fill on light slides |
| `COLORS.bodyGray` | `#595959` default body text on light slides |
| `COLORS.charcoal` | `#2E2E2E` strong body text on light slides |
| `COLORS.danger` | `#B91C1C` reserved for risk/error callouts |
| `FONTS.display` | `Urbanist` — cover/section titles |
| `FONTS.heading` | `Inter Medium` — slide titles |
| `FONTS.body` | `Inter` — default body |
| `FONTS.mono` | `DM Mono` — eyebrows, code, metric labels |
| `SIZES.cover` | 36 pt |
| `SIZES.sectionTitle` | 28 pt |
| `SIZES.slideTitle` | 20 pt |
| `SIZES.body` | 12 pt |
| `BULLETS.arrow` / `BULLETS.thin` | `➔` and `→` (use as `bullet: { code: "2794" }`) |

Pass these into raw `pptxgenjs` calls when you need a custom layout that the helpers don't cover.

## Slide builders

| Helper | What it produces |
|---|---|
| `addCoverSlide` | Full-bleed cover with eyebrow + display title + subtitle |
| `addSectionSlide` | Section divider with green hairline + section title |
| `addContentSlide` | Green pointer-arrow icon-box + title + arrow-bulleted body |
| `addStatSlide` | Row of large numeric callouts in vanilla cards |
| `addCompareSlide` | 2-4 panel cards with filled green header bars (large heading text); each card supports `variant: "vanilla" \| "green" \| "white"` |
| `addPanelsSlide` | 2-4 column panels with **UPPERCASE mono label** headers over a tall vanilla body. Each panel can hold free-form text, a flat bullet list, or grouped sub-sections — use this for "Our Understanding" / discovery layouts where each column groups multiple sub-sections under a category label |

Each helper accepts `theme: "light" | "dark"` to override the deck-wide default.
`addContentSlide`, `addCompareSlide`, and `addPanelsSlide` also accept `iconBox: false` to hide the title pointer-arrow.

### Multi-section / sub-section slides (`addPanelsSlide`)

This is the layout shown on Augment discovery slides — a slide title at the top
and 2-4 column panels below, each with an UPPERCASE category label header and
either free-form text, bullets, or grouped sub-sections.

```js
augment.addPanelsSlide(pptx, {
  title: "Our Understanding",
  panels: [
    {
      label: "Corporate Objectives",
      sections: [
        { title: "FY26 Goals", bullets: ["Grow ARR 2x", "Expand EMEA"] },
        { title: "North Star",  bullets: ["Time-to-PR < 1h"] },
      ],
    },
    {
      label: "Business Strategies",
      bullets: [
        "Land-and-expand in mid-market",
        "Tier-1 partner integrations",
      ],
    },
    {
      label: "Engineering Initiatives",
      body: "Free-form paragraph that flows naturally without bullets.",
    },
  ],
});
```

Each panel accepts **one** of `sections`, `bullets`, or `body` — pick the one
that matches the density of content you have.

## Visual motifs codified by this skill

- **Green pointer-arrow icon-box** next to slide titles (matches the green ➔ box seen on every content slide of the FMD reference deck).
- **Three-part footer** on every master: left "PRIVILEGED & CONFIDENTIAL", center wordmark with green dot + `augment code`, right year + slide number.
- **Panel cards** with filled green header bars over either a vanilla (`#F5F5F5`) body or a solid green body — switch via `variant`.
- **Light-by-default** — all slides including the closing slide stay white unless `theme: "dark"` is explicitly passed.

## Visual QA

After generating, verify in this order:
1. Open the file in Keynote/PowerPoint — fonts should be **Inter / Urbanist / DM Mono**. If the host machine is missing them, viewers will substitute; the layout still holds.
2. If you have LibreOffice + poppler, render to PNGs:
   ```bash
   soffice --headless --convert-to pdf deck.pptx
   pdftoppm -r 100 deck.pdf page -png
   ```
3. Spot-check: green pointer-arrow icon-box left of each title, three-part footer, and panel cards with filled green header bars.

## Source of truth

All tokens were extracted from `~/Downloads/Augment-FMD-Deck.pptx`. See `references/style-extraction.md` for the audit log (color frequency, font usage, bullet glyph counts across all 21 slides).

## Limitations

- Brand fonts are not embedded; the deck declares the typeface and relies on installation. To embed fonts, post-process with `python-pptx` or open in PowerPoint and "Save with embedded fonts".
- Logo assets are not bundled — pass image paths to `slide.addImage` yourself when needed.
- Helpers assume `LAYOUT_WIDE` (13.333" × 7.5"). Mixing in standard 4:3 layouts will require manual coordinate work.
