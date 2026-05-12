# auggie-skills

A collection of [Augment](https://www.augmentcode.com/) Skills — reusable
capabilities that the Augment Agent (and the `auggie` CLI) can discover and
invoke when their description matches what the user is asking for.

Each top-level folder is a self-contained skill: drop it into
`~/.augment/skills/` and Augment picks it up automatically.

## Skills in this repo

| Skill | What it does |
|---|---|
| [`auggie-deep-wiki/`](./auggie-deep-wiki) | Generates a DeepWiki-style MDX repository guide (with Mermaid diagrams) by orchestrating the headless `auggie` CLI in three steps: repo metadata → wiki structure → per-section MDX. Ships a browser viewer and static HTML export. |
| [`augment-pptx-corporate-style/`](./augment-pptx-corporate-style) | Builds PowerPoint decks that match the Augment Code corporate brand. Extends the base `pptx` skill with a typed theme (colors, fonts, sizes) and high-level `pptxgenjs` helpers (`addCoverSlide`, `addSectionSlide`, `addContentSlide`, `addStatSlide`, `addCompareSlide`). |
| [`perforce-scm/`](./perforce-scm) | Provides deep integration with Perforce (Helix Core) source control. Supports both `p4` CLI and P4Python SDK on Linux/macOS for syncing, editing, submitting changelists, and managing streams or classic branches. |

See each skill's own `SKILL.md` and `README.md` for full triggers, prerequisites,
and usage details.

## Installing a skill

Skills are discovered from `~/.augment/skills/<name>/SKILL.md`. To install one
from this repo:

```bash
git clone https://github.com/augment-solutions/auggie-skills.git
cd auggie-skills

# Pick the skill(s) you want — copy or symlink into the Augment skills dir
cp -r auggie-deep-wiki ~/.augment/skills/
cp -r augment-pptx-corporate-style ~/.augment/skills/
cp -r perforce-scm ~/.augment/skills/

# Or symlink so future `git pull` updates apply automatically:
ln -s "$PWD/auggie-deep-wiki" ~/.augment/skills/auggie-deep-wiki
ln -s "$PWD/augment-pptx-corporate-style" ~/.augment/skills/augment-pptx-corporate-style
ln -s "$PWD/perforce-scm" ~/.augment/skills/perforce-scm
```

After copying/linking, restart your Augment client (VS Code extension, JetBrains
plugin, or `auggie` CLI session) so the new skills are picked up.

### Per-skill setup

Some skills have optional dependencies. See each skill's `README.md`, but at a
glance:

- **`auggie-deep-wiki`** — Python 3.10+ and `git` are required. Optional
  Node.js + `npm install` inside the skill folder enables MDX validation
  (`@mdx-js/mdx`). The browser viewer uses CDN-hosted `marked` and `mermaid`,
  no install needed.
- **`augment-pptx-corporate-style`** — Node.js + `pptxgenjs`. Run `npm install`
  inside the skill folder.
- **`perforce-scm`** — `p4` CLI and/or `p4python` (`pip install p4python`) are required. Linux/macOS shell environment.

## Repository layout

```
auggie-skills/
├── README.md                            # This file
├── .gitignore                           # Standard Node template + skill-specific ignores
├── auggie-deep-wiki/                    # Skill #1
│   └── SKILL.md, README.md, scripts/, prompts/, references/, preview/, …
└── augment-pptx-corporate-style/        # Skill #2
    └── SKILL.md, index.js, theme.js, slide-helpers.js, examples/, references/
└── perforce-scm/                        # Skill #3
    └── SKILL.md, README.md, references/
```

## Contributing a new skill

1. Create a new folder at the repo root named after your skill (matching the
   `name:` field in its `SKILL.md` frontmatter).
2. Add a `SKILL.md` with frontmatter (`name`, `description`) — the description
   is what Augment uses to decide whether to invoke your skill, so be specific
   about triggers and use cases.
3. Add a `README.md` with human-facing setup, usage, and troubleshooting.
4. Keep the skill self-contained: any helper scripts, prompts, references, or
   templates should live inside the skill folder so it can be installed in
   isolation.
5. If the skill depends on a runtime (Node, Python, etc.), document the
   prerequisites and provide a lockfile (`package-lock.json`, `requirements.txt`,
   etc.) so installs are reproducible.

For more on how Augment Skills work, see Anthropic's reference marketplace at
<https://github.com/anthropics/anthropic-agent-skills>.

## License

[MIT](./LICENSE) © Augment Solutions
