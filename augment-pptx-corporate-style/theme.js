// Augment corporate slide theme for pptxgenjs.
// Style tokens were extracted from ~/Downloads/Augment-FMD-Deck.pptx
// (theme XMLs, slide masters, and per-slide overrides across all 21 slides).
// See references/style-extraction.md for the audit details.

"use strict";

// -----------------------------------------------------------------------------
// Color palette (hex strings WITHOUT leading '#', as pptxgenjs expects).
// -----------------------------------------------------------------------------
const COLORS = {
  // Primary brand
  green: "008236",          // Augment Green - dominant brand accent (166 uses)
  periwinkle: "99A2FF",     // Secondary accent (91 uses)
  periwinkleSoft: "B8C2FF", // Tint of periwinkle
  periwinkleTint: "DADDFF", // Even softer tint, used for backgrounds

  // Neutrals
  black: "000000",          // Default dark master background
  white: "FFFFFF",
  offWhite: "F5F5F5",       // Light card/background fill (82 uses)
  offWhiteDim: "EFEFEF",
  paleGray: "E5E5E5",
  midGrayLight: "D1D5DB",
  midGray: "8A8A8A",        // Muted body text on dark backgrounds (93 uses)
  bodyGray: "595959",       // Dominant body-text color on light slides (139 uses)
  charcoal: "2E2E2E",       // Strong body text on light slides (91 uses)
  ink: "111827",            // Headline ink for light slides

  // Semantic
  warn: "F2BC8C",           // Warm accent
  highlight: "F6FF93",      // Highlight yellow (sparingly)
  danger: "B91C1C",         // Reserved for error/risk callouts
};

// -----------------------------------------------------------------------------
// Typography. Augment's corporate standard (per Google Slides) is "Inter"
// used as a single family at its natural Regular weight - the FMD reference
// deck has Inter at only ~16% bold across all sizes and 0% bold at title
// sizes (20-36pt), so we avoid `bold: true` on Inter text and let the
// typeface itself carry the visual weight. DM Mono is retained for the
// wordmark and code/metric labels (it can be bold for label-style emphasis).
// pptxgenjs cannot embed fonts, so we set the typeface name and assume the
// viewer has them installed (or PowerPoint substitutes gracefully).
// Get the fonts here: https://fonts.google.com/specimen/Inter
//                     https://fonts.google.com/specimen/DM+Mono
// -----------------------------------------------------------------------------
const FONTS = {
  display: "Inter",            // Big cover/section titles - regular weight
  heading: "Inter",            // Slide titles and subheads - regular weight
  body: "Inter",               // Default body
  bodyLight: "Inter",          // Quiet labels, captions
  mono: "DM Mono",             // Code, metric labels, eyebrow tags
  monoMedium: "DM Mono",       // alias kept for back-compat
};

// Sizes are in points. Derived from the actual reference deck distribution.
const SIZES = {
  cover: 36,
  sectionTitle: 28,
  slideTitle: 20,
  subTitle: 16,
  bodyLg: 14,
  body: 12,
  bodySm: 10,
  caption: 9,
  micro: 8,
  eyebrow: 9,    // small UPPERCASE label above titles, usually mono
};

// Bullet glyphs observed in the deck.
const BULLETS = {
  arrow: "\u2794",  // ➔ - primary bullet (43 uses)
  thin:  "\u2192",  // → - secondary/inline arrow (18 uses)
  dot:   "\u25CF",  // ●
  square:"\u25A0",  // ■
};

// Standard slide geometry for LAYOUT_WIDE (13.333" x 7.5").
const LAYOUT = {
  name: "AUGMENT_WIDE",
  width: 13.333,
  height: 7.5,
  margin: 0.5,
  gutter: 0.25,
};

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

/**
 * Apply the Augment-branded layout + master slides to a fresh pptxgenjs deck.
 * Returns the same `pptx` instance for chaining.
 *
 * @param {object} pptx - a `new PptxGenJS()` instance
 * @param {object} [opts]
 * @param {"light"|"dark"} [opts.defaultTheme="light"]
 * @param {string} [opts.author="Augment Code"]
 * @param {string} [opts.company="Augment Code"]
 * @param {string} [opts.title]
 * @param {string} [opts.confidentiality="PRIVILEGED & CONFIDENTIAL"] footer-left
 * @param {string} [opts.footerYear=current year] footer-right prefix
 * @param {string} [opts.wordmark="augment code"] footer-center wordmark
 */
function applyTheme(pptx, opts = {}) {
  const {
    defaultTheme = "light",
    author = "Augment Code",
    company = "Augment Code",
    title,
    confidentiality = "PRIVILEGED & CONFIDENTIAL",
    footerYear = String(new Date().getFullYear()),
    wordmark = "augment code",
  } = opts;

  pptx.defineLayout({ name: LAYOUT.name, width: LAYOUT.width, height: LAYOUT.height });
  pptx.layout = LAYOUT.name;
  pptx.author = author;
  pptx.company = company;
  if (title) pptx.title = title;

  // Three-part footer pattern shared by both masters.
  const footerY = 7.18;
  const footerH = 0.22;
  function footerObjects(textColor) {
    return [
      // Top hairline divider above the footer
      { rect: { x: LAYOUT.margin, y: footerY - 0.10, w: LAYOUT.width - 2 * LAYOUT.margin,
        h: 0.005, fill: { color: COLORS.midGrayLight } } },
      // Left: confidentiality label
      { text: { text: confidentiality, options: {
        x: LAYOUT.margin, y: footerY, w: 4, h: footerH,
        fontFace: FONTS.mono, fontSize: 7,
        color: textColor, charSpacing: 2 } } },
      // Center: wordmark (small green dot + lowercase mono text)
      { rect: { x: LAYOUT.width / 2 - 0.55, y: footerY + 0.05, w: 0.10, h: 0.10,
        fill: { color: COLORS.green }, line: { color: COLORS.green } } },
      { text: { text: wordmark, options: {
        x: LAYOUT.width / 2 - 0.40, y: footerY, w: 1.6, h: footerH,
        fontFace: FONTS.mono, fontSize: 8, color: textColor } } },
      // Right: year (page number is added per-slide via slideNumber prop)
      { text: { text: footerYear, options: {
        x: LAYOUT.width - LAYOUT.margin - 1.0, y: footerY, w: 1.0, h: footerH,
        fontFace: FONTS.mono, fontSize: 7, color: textColor, align: "right" } } },
    ];
  }
  const slideNumberStyle = (color) => ({
    x: LAYOUT.width - LAYOUT.margin - 0.5, y: footerY + 0.18, w: 0.5, h: footerH,
    fontFace: FONTS.mono, fontSize: 7, color, align: "right",
  });

  pptx.defineSlideMaster({
    title: "AUGMENT_LIGHT",
    background: { color: COLORS.white },
    objects: footerObjects(COLORS.midGray),
    slideNumber: slideNumberStyle(COLORS.midGray),
  });

  pptx.defineSlideMaster({
    title: "AUGMENT_DARK",
    background: { color: COLORS.black },
    objects: footerObjects(COLORS.midGray),
    slideNumber: slideNumberStyle(COLORS.midGray),
  });

  pptx._defaultMaster = defaultTheme === "dark" ? "AUGMENT_DARK" : "AUGMENT_LIGHT";
  return pptx;
}

module.exports = {
  COLORS, FONTS, SIZES, BULLETS, LAYOUT,
  applyTheme,
};
