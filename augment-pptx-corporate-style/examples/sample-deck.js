// End-to-end example: generate a small branded deck using the skill helpers.
// Run from the skill directory:
//   NODE_PATH=$(npm root -g) node examples/sample-deck.js

const PptxGenJS = require("pptxgenjs");
const augment = require("..");

const pptx = augment.applyTheme(new PptxGenJS(), {
  defaultTheme: "light",
  title: "Augment Style Sample Deck",
});

augment.addCoverSlide(pptx, {
  eyebrow: "Brand Style Sample",
  title: "The Software Agent Company",
  subtitle: "A reference deck generated entirely from the augment-slides-skill helpers.",
});

augment.addSectionSlide(pptx, {
  eyebrow: "Section 01",
  title: "Why now",
  subtitle: "The market shifted, and so did our delivery model.",
});

augment.addContentSlide(pptx, {
  eyebrow: "Context",
  title: "Three forces converging",
  bullets: [
    "AI-generated code volume is exploding across the SDLC",
    { text: "Reviewer attention is the new bottleneck", level: 0 },
    { text: "Subtle defects slip past CI when context is missing", level: 1 },
    { text: "Production incidents follow weeks later", level: 1 },
    "Augment closes the gap between code written and production-ready",
  ],
});

augment.addStatSlide(pptx, {
  title: "What elite teams achieve with Augment",
  stats: [
    { value: "+20%", label: "Throughput per engineer" },
    { value: "30s", label: "Mean time from failure to fix" },
    { value: "60K", label: "Reviews automated per quarter", accent: augment.COLORS.periwinkle },
  ],
});

augment.addCompareSlide(pptx, {
  title: "Two delivery surfaces, one platform",
  cards: [
    {
      title: "IDE companion",
      variant: "vanilla",
      bullets: [
        "Inline edits and chat",
        "Local context engine",
        "Zero-config setup",
      ],
    },
    {
      title: "GitHub automation",
      variant: "green",
      bullets: [
        "PR-triggered agents",
        "Companion-branch workflow",
        "Posts results back as comments",
      ],
    },
  ],
});

// "Our Understanding" - multi-section / sub-section panels (matches the
// reference discovery slide pattern).
augment.addPanelsSlide(pptx, {
  title: "Our Understanding",
  panels: [
    {
      label: "Corporate Objectives",
      sections: [
        { title: "FY26 GOALS",  bullets: ["Grow ARR 2x", "Expand EMEA"] },
        { title: "NORTH STAR",  bullets: ["Time-to-PR < 1h"] },
      ],
    },
    {
      label: "Business Strategies",
      bullets: [
        "Land-and-expand in mid-market",
        "Tier-1 partner integrations",
        "Verticalized go-to-market",
      ],
    },
    {
      label: "Engineering Initiatives",
      body: "Ship the unified context engine, lift agent throughput, and harden the multi-tenant deploy path.",
    },
  ],
});

augment.addCoverSlide(pptx, {
  eyebrow: "Wrap",
  title: "Thank you",
  subtitle: "Questions, ideas, requests — we read them all.",
});

(async () => {
  const out = await pptx.writeFile({ fileName: "augment-style-sample.pptx" });
  console.log("Wrote", out);
})();
