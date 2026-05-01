// @ts-check
import { defineConfig } from "astro/config";
import mdx from "@astrojs/mdx";

// Static Astro site. Vercel auto-detects Astro and runs `astro build`,
// serving the `dist/` directory from its global CDN. No adapter required
// for static output.
export default defineConfig({
  site: process.env.ASTRO_SITE_URL ?? "https://example.vercel.app",
  integrations: [mdx()],
  markdown: {
    shikiConfig: {
      theme: "github-dark-dimmed",
      wrap: true,
    },
  },
});
