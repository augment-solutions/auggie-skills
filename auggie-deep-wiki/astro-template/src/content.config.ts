import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

// One entry per published wiki, keyed by `<slug>` and stored at
// `src/content/wikis/<slug>/index.mdx`. Each entry's frontmatter is
// validated against the schema below; extra fields are tolerated.
const wikis = defineCollection({
  loader: glob({ pattern: "**/index.mdx", base: "./src/content/wikis" }),
  schema: z.object({
    title: z.string(),
    // Accept any non-empty string so SSH remotes (`git@github.com:org/repo`)
    // and local paths (`/Users/me/repo`) don't fail content validation.
    // Layouts only render ``repo_url`` as a link when it parses as http(s).
    repo_url: z.string().min(1).optional(),
    description: z.string().optional(),
    last_updated: z.string().optional(),
    commit_hash: z.string().optional(),
    commit_hash_short: z.string().optional(),
    stars: z.number().optional(),
    language: z.string().optional(),
    topics: z.array(z.string()).optional(),
  }),
});

export const collections = { wikis };
