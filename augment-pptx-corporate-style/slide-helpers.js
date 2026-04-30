// High-level slide builders for the Augment corporate theme.
// Each helper takes a pptx instance + a small options object and adds a slide.
// Returns the slide so callers can append extra content if needed.

"use strict";
const { COLORS, FONTS, SIZES, BULLETS, LAYOUT } = require("./theme");

const W = LAYOUT.width;
const M = LAYOUT.margin;

function _resolveMaster(pptx, themeOpt) {
  if (themeOpt === "dark") return "AUGMENT_DARK";
  if (themeOpt === "light") return "AUGMENT_LIGHT";
  return pptx._defaultMaster || "AUGMENT_LIGHT";
}

function _isDark(masterName) {
  return masterName === "AUGMENT_DARK";
}

/**
 * Internal: draw a small green square with a white arrow centered inside.
 * Used as a brand title icon and as a section-bullet marker.
 */
function _iconBox(slide, x, y, size, opts = {}) {
  const fill = opts.fill || COLORS.green;
  const glyph = opts.glyph || "\u2794"; // ➔
  slide.addShape("rect", {
    x, y, w: size, h: size,
    fill: { color: fill }, line: { color: fill },
  });
  slide.addText(glyph, {
    x, y, w: size, h: size,
    fontFace: FONTS.body, fontSize: Math.round(size * 30), bold: true,
    color: COLORS.white, align: "center", valign: "middle",
    margin: 0,
  });
}

/**
 * Cover slide: large display title, optional subtitle and eyebrow tag.
 * @param {object} pptx
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} [opts.subtitle]
 * @param {string} [opts.eyebrow]   small UPPERCASE label above the title
 * @param {"light"|"dark"} [opts.theme]
 */
function addCoverSlide(pptx, opts) {
  const master = _resolveMaster(pptx, opts.theme);
  const dark = _isDark(master);
  const slide = pptx.addSlide({ masterName: master });
  const titleColor = dark ? COLORS.white : COLORS.ink;
  const subColor = dark ? COLORS.midGrayLight : COLORS.bodyGray;

  if (opts.eyebrow) {
    slide.addText(opts.eyebrow.toUpperCase(), {
      x: M, y: 2.2, w: W - 2 * M, h: 0.4,
      fontFace: FONTS.mono, fontSize: SIZES.eyebrow,
      color: COLORS.green, charSpacing: 4, bold: true,
    });
  }
  slide.addText(opts.title, {
    x: M, y: 2.6, w: W - 2 * M, h: 1.6,
    fontFace: FONTS.display, fontSize: SIZES.cover,
    color: titleColor,
  });
  if (opts.subtitle) {
    slide.addText(opts.subtitle, {
      x: M, y: 4.4, w: W - 2 * M, h: 1.0,
      fontFace: FONTS.bodyLight, fontSize: SIZES.subTitle,
      color: subColor,
    });
  }
  return slide;
}

/**
 * Section divider slide: large display title centered, green hairline above.
 */
function addSectionSlide(pptx, opts) {
  const master = _resolveMaster(pptx, opts.theme);
  const dark = _isDark(master);
  const slide = pptx.addSlide({ masterName: master });
  const titleColor = dark ? COLORS.white : COLORS.ink;

  slide.addShape("rect", {
    x: M, y: 3.2, w: 0.6, h: 0.06, fill: { color: COLORS.green }, line: { color: COLORS.green },
  });
  if (opts.eyebrow) {
    slide.addText(opts.eyebrow.toUpperCase(), {
      x: M, y: 3.35, w: W - 2 * M, h: 0.4,
      fontFace: FONTS.mono, fontSize: SIZES.eyebrow,
      color: COLORS.green, charSpacing: 4, bold: true,
    });
  }
  slide.addText(opts.title, {
    x: M, y: 3.7, w: W - 2 * M, h: 1.4,
    fontFace: FONTS.display, fontSize: SIZES.sectionTitle,
    color: titleColor,
  });
  if (opts.subtitle) {
    slide.addText(opts.subtitle, {
      x: M, y: 5.0, w: W - 2 * M, h: 0.8,
      fontFace: FONTS.body, fontSize: SIZES.bodyLg,
      color: dark ? COLORS.midGrayLight : COLORS.bodyGray,
    });
  }
  return slide;
}

/**
 * Content slide: title bar + bullet list. Bullets use the brand arrow glyph.
 * The title is preceded by a small green icon-box (the brand "pointer arrow").
 * @param {object} opts
 * @param {string} opts.title
 * @param {string[]|object[]} opts.bullets - either strings or {text, level}
 * @param {string} [opts.eyebrow]
 * @param {boolean} [opts.iconBox=true] draw the green pointer-arrow box next to title
 */
function addContentSlide(pptx, opts) {
  const master = _resolveMaster(pptx, opts.theme);
  const dark = _isDark(master);
  const slide = pptx.addSlide({ masterName: master });
  const titleColor = dark ? COLORS.white : COLORS.ink;
  const bodyColor = dark ? COLORS.midGrayLight : COLORS.bodyGray;

  const eyebrowY = M;
  const titleY = opts.eyebrow ? M + 0.32 : M;
  const showIcon = opts.iconBox !== false;
  const iconSize = 0.5;
  const titleX = showIcon ? M + iconSize + 0.18 : M;

  if (opts.eyebrow) {
    slide.addText(opts.eyebrow.toUpperCase(), {
      x: titleX, y: eyebrowY, w: W - titleX - M, h: 0.3,
      fontFace: FONTS.mono, fontSize: SIZES.eyebrow,
      color: COLORS.green, charSpacing: 4, bold: true,
    });
  }
  if (showIcon) {
    _iconBox(slide, M, titleY + 0.05, iconSize);
  }
  slide.addText(opts.title, {
    x: titleX, y: titleY, w: W - titleX - M, h: 0.7,
    fontFace: FONTS.heading, fontSize: SIZES.slideTitle,
    color: titleColor, valign: "middle",
  });
  // Hairline under title
  slide.addShape("line", {
    x: M, y: titleY + 0.85, w: W - 2 * M, h: 0,
    line: { color: COLORS.midGrayLight, width: 0.75 },
  });

  const items = (opts.bullets || []).map((b) => {
    const text = typeof b === "string" ? b : b.text;
    const level = typeof b === "string" ? 0 : (b.level || 0);
    return {
      text,
      options: {
        bullet: { code: level === 0 ? "2794" : "2192", indent: 18 },
        paraSpaceAfter: 6, indentLevel: level,
        color: level === 0 ? (dark ? COLORS.white : COLORS.charcoal) : bodyColor,
      },
    };
  });
  slide.addText(items, {
    x: M, y: titleY + 1.05, w: W - 2 * M, h: 5.0,
    fontFace: FONTS.body, fontSize: SIZES.bodyLg,
    color: bodyColor, valign: "top",
  });
  return slide;
}

/**
 * Metric/stat slide: a row of large numeric callouts with labels.
 * @param {object} opts
 * @param {string} opts.title
 * @param {Array<{value:string,label:string,accent?:string}>} opts.stats
 */
function addStatSlide(pptx, opts) {
  const master = _resolveMaster(pptx, opts.theme);
  const dark = _isDark(master);
  const slide = pptx.addSlide({ masterName: master });
  const titleColor = dark ? COLORS.white : COLORS.ink;
  const labelColor = dark ? COLORS.midGrayLight : COLORS.bodyGray;

  slide.addText(opts.title, {
    x: M, y: M, w: W - 2 * M, h: 0.7,
    fontFace: FONTS.heading, fontSize: SIZES.slideTitle, color: titleColor,
  });

  const stats = opts.stats || [];
  const cardW = (W - 2 * M - (stats.length - 1) * 0.3) / stats.length;
  stats.forEach((s, i) => {
    const x = M + i * (cardW + 0.3);
    slide.addShape("rect", {
      x, y: 2.4, w: cardW, h: 3.0,
      fill: { color: dark ? COLORS.charcoal : COLORS.offWhite },
      line: { color: dark ? COLORS.charcoal : COLORS.offWhite },
    });
    slide.addText(s.value, {
      x: x + 0.2, y: 2.7, w: cardW - 0.4, h: 1.4,
      fontFace: FONTS.display, fontSize: 56,
      color: s.accent || COLORS.green, valign: "middle",
    });
    slide.addText(s.label, {
      x: x + 0.2, y: 4.2, w: cardW - 0.4, h: 1.0,
      fontFace: FONTS.body, fontSize: SIZES.bodyLg,
      color: labelColor,
    });
  });
  return slide;
}

/**
 * Comparison slide: 2..4 panel-style cards side-by-side.
 * Cards have a filled green header (white title text) over a vanilla body.
 * Per-card `variant` overrides the body fill: "vanilla" (default), "green",
 * "white", or any hex string. When variant is "green", the body uses a soft
 * green tint and the header is darker green.
 * @param {object} opts
 * @param {string} opts.title
 * @param {Array<{title:string,bullets:string[],variant?:string,accent?:string}>} opts.cards
 */
function addCompareSlide(pptx, opts) {
  const master = _resolveMaster(pptx, opts.theme);
  const dark = _isDark(master);
  const slide = pptx.addSlide({ masterName: master });
  const titleColor = dark ? COLORS.white : COLORS.ink;

  // Title with the brand icon-box, matching addContentSlide.
  const showIcon = opts.iconBox !== false;
  const iconSize = 0.5;
  const titleX = showIcon ? M + iconSize + 0.18 : M;
  if (showIcon) _iconBox(slide, M, M + 0.05, iconSize);
  slide.addText(opts.title, {
    x: titleX, y: M, w: W - titleX - M, h: 0.7,
    fontFace: FONTS.heading, fontSize: SIZES.slideTitle,
    color: titleColor, valign: "middle",
  });
  slide.addShape("line", {
    x: M, y: M + 0.85, w: W - 2 * M, h: 0,
    line: { color: COLORS.midGrayLight, width: 0.75 },
  });

  const cards = opts.cards || [];
  const gap = 0.25;
  const cardW = (W - 2 * M - (cards.length - 1) * gap) / cards.length;
  const cardY = 1.85, cardH = 5.0;
  const headerH = 0.7;
  cards.forEach((c, i) => {
    const x = M + i * (cardW + gap);
    const variant = c.variant || "vanilla";
    let bodyFill, headerFill, headerText;
    switch (variant) {
      case "green":
        bodyFill = COLORS.green;
        headerFill = COLORS.green;
        headerText = COLORS.white;
        break;
      case "white":
        bodyFill = COLORS.white;
        headerFill = c.accent || COLORS.green;
        headerText = COLORS.white;
        break;
      case "vanilla":
      default:
        bodyFill = COLORS.offWhite;
        headerFill = c.accent || COLORS.green;
        headerText = COLORS.white;
        break;
    }
    // Body panel
    slide.addShape("rect", {
      x, y: cardY, w: cardW, h: cardH,
      fill: { color: bodyFill },
      line: { color: variant === "white" ? COLORS.midGrayLight : bodyFill, width: 0.5 },
    });
    // Filled header bar
    slide.addShape("rect", {
      x, y: cardY, w: cardW, h: headerH,
      fill: { color: headerFill }, line: { color: headerFill },
    });
    slide.addText(c.title, {
      x: x + 0.2, y: cardY, w: cardW - 0.4, h: headerH,
      fontFace: FONTS.heading, fontSize: SIZES.subTitle,
      color: headerText, valign: "middle",
    });
    const bulletColor = variant === "green" ? COLORS.white : COLORS.charcoal;
    const items = (c.bullets || []).map((t) => {
      const text = typeof t === "string" ? t : t.text;
      const level = typeof t === "string" ? 0 : (t.level || 0);
      return {
        text,
        options: {
          bullet: { code: level === 0 ? "2794" : "2192", indent: 16 },
          paraSpaceAfter: 6, indentLevel: level,
          color: bulletColor,
        },
      };
    });
    slide.addText(items, {
      x: x + 0.2, y: cardY + headerH + 0.15, w: cardW - 0.4, h: cardH - headerH - 0.3,
      fontFace: FONTS.body, fontSize: SIZES.body,
      color: bulletColor, valign: "top",
    });
  });
  return slide;
}

/**
 * Multi-panel slide: 2-4 column panels, each with an UPPERCASE mono label
 * header (green bar, white text) over a tall vanilla body that holds either
 * free-form text, a flat bullet list, or grouped sub-sections.
 *
 * This is the layout used on Augment "Our Understanding" / discovery slides
 * where each column groups several sub-sections under a category label.
 *
 * @param {object} pptx
 * @param {object} opts
 * @param {string} opts.title                    Slide title (gets the green icon-box)
 * @param {string} [opts.eyebrow]
 * @param {boolean} [opts.iconBox=true]
 * @param {Array<object>} opts.panels            2..4 column panels. Each panel:
 *   - {string} label                            UPPERCASE category label for header bar
 *   - {string} [body]                           Free-form paragraph text
 *   - {string[]|object[]} [bullets]             Flat bullet list ({text, level} ok)
 *   - {Array<{title?:string, bullets:string[]}>} [sections]  Grouped sub-sections
 *   - {string} [variant="vanilla"]              "vanilla" | "white" | "green"
 *   - {string} [headerFill]                     Override header color (hex w/o #)
 */
function addPanelsSlide(pptx, opts) {
  const master = _resolveMaster(pptx, opts.theme);
  const dark = _isDark(master);
  const slide = pptx.addSlide({ masterName: master });
  const titleColor = dark ? COLORS.white : COLORS.ink;

  // Title row with brand icon-box (matches addContentSlide / addCompareSlide).
  const showIcon = opts.iconBox !== false;
  const iconSize = 0.5;
  const titleX = showIcon ? M + iconSize + 0.18 : M;
  if (showIcon) _iconBox(slide, M, M + 0.05, iconSize);
  if (opts.eyebrow) {
    slide.addText(opts.eyebrow.toUpperCase(), {
      x: titleX, y: M, w: W - titleX - M, h: 0.3,
      fontFace: FONTS.mono, fontSize: SIZES.eyebrow,
      color: COLORS.green, charSpacing: 4, bold: true,
    });
  }
  slide.addText(opts.title, {
    x: titleX, y: opts.eyebrow ? M + 0.32 : M, w: W - titleX - M, h: 0.7,
    fontFace: FONTS.heading, fontSize: SIZES.slideTitle,
    color: titleColor, valign: "middle",
  });
  slide.addShape("line", {
    x: M, y: (opts.eyebrow ? M + 0.32 : M) + 0.85, w: W - 2 * M, h: 0,
    line: { color: COLORS.midGrayLight, width: 0.75 },
  });

  // Panel grid
  const panels = opts.panels || [];
  const gap = 0.25;
  const panelW = (W - 2 * M - (panels.length - 1) * gap) / panels.length;
  const headerH = 0.45;
  const panelY = 1.85;
  const panelH = 5.0;

  panels.forEach((p, i) => {
    const x = M + i * (panelW + gap);
    const variant = p.variant || "vanilla";
    const bodyFill = variant === "green" ? COLORS.green
                  : variant === "white" ? COLORS.white
                  : COLORS.offWhite;
    const headerFill = p.headerFill || COLORS.green;
    const headerText = COLORS.white;
    const textColor = variant === "green" ? COLORS.white : COLORS.charcoal;
    const subColor = variant === "green" ? COLORS.white : COLORS.bodyGray;

    // Green header bar
    slide.addShape("rect", {
      x, y: panelY, w: panelW, h: headerH,
      fill: { color: headerFill }, line: { color: headerFill },
    });
    slide.addText(String(p.label || "").toUpperCase(), {
      x: x + 0.2, y: panelY, w: panelW - 0.4, h: headerH,
      fontFace: FONTS.mono, fontSize: 10, charSpacing: 3,
      color: headerText, bold: true, valign: "middle",
    });
    // Vanilla body panel directly below header
    const bodyY = panelY + headerH;
    const bodyH = panelH - headerH;
    slide.addShape("rect", {
      x, y: bodyY, w: panelW, h: bodyH,
      fill: { color: bodyFill },
      line: { color: variant === "white" ? COLORS.midGrayLight : bodyFill, width: 0.5 },
    });

    const innerX = x + 0.25;
    const innerW = panelW - 0.5;
    const innerY = bodyY + 0.2;
    const innerH = bodyH - 0.4;

    if (p.sections && p.sections.length) {
      // Grouped sub-sections: small label + bullets, stacked vertically.
      const items = [];
      p.sections.forEach((sec, si) => {
        if (sec.title) {
          items.push({ text: sec.title, options: {
            fontFace: FONTS.mono, fontSize: 8, color: COLORS.green, bold: true,
            charSpacing: 2, paraSpaceBefore: si === 0 ? 0 : 8, paraSpaceAfter: 4,
          } });
        }
        (sec.bullets || []).forEach((b) => {
          const text = typeof b === "string" ? b : b.text;
          const level = typeof b === "string" ? 0 : (b.level || 0);
          items.push({ text, options: {
            bullet: { code: level === 0 ? "2794" : "2192", indent: 14 },
            paraSpaceAfter: 4, indentLevel: level,
            fontFace: FONTS.body, fontSize: SIZES.bodySm,
            color: level === 0 ? textColor : subColor,
          } });
        });
      });
      slide.addText(items, { x: innerX, y: innerY, w: innerW, h: innerH, valign: "top" });
    } else if (p.bullets && p.bullets.length) {
      const items = p.bullets.map((b) => {
        const text = typeof b === "string" ? b : b.text;
        const level = typeof b === "string" ? 0 : (b.level || 0);
        return { text, options: {
          bullet: { code: level === 0 ? "2794" : "2192", indent: 14 },
          paraSpaceAfter: 6, indentLevel: level,
          color: level === 0 ? textColor : subColor,
        } };
      });
      slide.addText(items, {
        x: innerX, y: innerY, w: innerW, h: innerH,
        fontFace: FONTS.body, fontSize: SIZES.body,
        color: textColor, valign: "top",
      });
    } else if (p.body) {
      slide.addText(p.body, {
        x: innerX, y: innerY, w: innerW, h: innerH,
        fontFace: FONTS.body, fontSize: SIZES.body,
        color: textColor, valign: "top",
      });
    }
  });
  return slide;
}

module.exports = {
  addCoverSlide,
  addSectionSlide,
  addContentSlide,
  addStatSlide,
  addCompareSlide,
  addPanelsSlide,
};
