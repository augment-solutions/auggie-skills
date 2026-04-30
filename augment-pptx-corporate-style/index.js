// Augment corporate slide theme - public entrypoint.
// Usage:
//   const PptxGenJS = require("pptxgenjs");
//   const augment = require("./path/to/augment-slides-skill");
//   const pptx = augment.applyTheme(new PptxGenJS(), { defaultTheme: "light" });
//   augment.addCoverSlide(pptx, { title: "Hello", subtitle: "World" });
//   pptx.writeFile({ fileName: "out.pptx" });

"use strict";

const theme = require("./theme");
const helpers = require("./slide-helpers");

module.exports = {
  ...theme,
  ...helpers,
};
