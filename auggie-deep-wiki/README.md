# auggie-deep-wiki

Generate a [DeepWiki](https://deepwiki.com)-style MDX guide for any Git
repository by orchestrating the headless [`auggie`](https://docs.augmentcode.com)
CLI.

This is an **Augment skill**, discovered automatically by an Augment-powered
agent (Auggie, Claude Code with Augment, etc.) when you ask it to "generate a
deep wiki" or similar. You can also run the orchestrator directly from the
command line — see below.

## Layout

```
auggie-deep-wiki/
├── SKILL.md                       # Discovery + agent guidance (frontmatter)
├── README.md                      # This file (human-facing)
├── package.json                   # Optional: `npm install` for MDX validator
├── scripts/
│   ├── generate_wiki.py           # stdlib-only Python orchestrator
│   ├── build_static.py            # bundle wiki.mdx into a self-contained index.html
│   ├── preview.py                 # stdlib-only HTTP server for viewing wiki.mdx
│   └── validate_mdx.mjs           # Optional Node MDX validator
├── preview/
│   └── index.html                 # Viewer template (marked + mermaid via CDN)
├── prompts/                       # LLM instructions used by each step
│   ├── repo_metadata.txt
│   ├── wiki_structure.txt
│   └── wiki_section.txt
└── references/
    └── architecture.md            # On-demand pipeline reference
```

## Prerequisites

- `auggie` on `$PATH` (verify with `auggie --version`)
- A valid Augment auth context (one of):
  - `~/.augment/session.json` (created by `auggie login`)
  - `AUGMENT_API_TOKEN` exported in your environment
- `git` on `$PATH`
- Python 3.10+
- **Optional**, for MDX validation: Node.js + `@mdx-js/mdx`
  (`npm install` in this directory)

## Quick start (CLI)

```bash
# Install the optional MDX validator (one-time, optional)
cd ~/.augment/skills/auggie-deep-wiki
npm install     # pulls @mdx-js/mdx so validate_mdx.mjs can run

# Generate a wiki
python3 scripts/generate_wiki.py \
  https://github.com/pallets/click \
  --output-dir ./output/click \
  --model haiku4.5
```

Output:

```
output/click/
├── wiki.mdx               # The assembled, MDX-validated wiki
├── wiki_structure.json    # The TOC produced by step 2
├── repo_metadata.json     # Merged metadata (LLM + GitHub API + git HEAD)
└── sections/
    ├── overview.mdx
    ├── architecture.mdx
    └── …
```

Run with `--help` for all flags. The most useful ones during iteration:

| Flag                       | Why                                          |
| -------------------------- | -------------------------------------------- |
| `--no-cleanup`             | Keep workspace + cache for post-mortem       |
| `--workspace-dir <path>`   | Reuse a clone (skips `git clone`)            |
| `--cache-dir <path>`       | Reuse the auggie index across runs           |
| `--skip-validate`          | Skip the Node MDX validation pass            |
| `--verbose`                | Debug logs (raises tracebacks on failure)    |
| `--model sonnet4`          | Higher-quality but slower / more expensive   |

## Viewing the output

Both modes parse Markdown via [`marked`](https://marked.js.org/) and render
fenced ```` ```mermaid ```` blocks with [`mermaid`](https://mermaid.js.org/),
both pulled from a CDN. They auto-detect `prefers-color-scheme`, build a
sticky table of contents from `##`/`###` headings, and render the native
HTML tags (`<details>`, `<summary>`, `<ul>`) the generator emits.

### Static viewer (default — opens via `file://`)

`generate_wiki.py` writes `<output-dir>/index.html` automatically (skip
with `--no-static`). Open it directly in any browser:

```bash
open ./output/click/index.html        # macOS
xdg-open ./output/click/index.html    # Linux
start .\output\click\index.html       # Windows
```

The whole `<output-dir>` is self-contained — zip/tar it and ship the
archive to anyone with a browser. The HTML is ~38 KB and embeds `wiki.mdx`
inline as a `<script type="text/markdown">` block, so no separate file
fetch happens.

To re-bundle an existing output (or after editing `wiki.mdx` by hand):

```bash
python3 scripts/build_static.py ./output/click
# Wrote ./output/click/index.html (37.4 KB)
```

### Live server (handy when iterating on prompts)

```bash
python3 scripts/preview.py ./output/click
# → opens http://127.0.0.1:8765/ in your default browser
```

`preview.py` serves the bundled `index.html` if one exists in the output
dir (matching exactly what `file://` shows); otherwise it falls back to
the unbundled `preview/index.html` template that fetches `wiki.mdx`
dynamically.

| Flag        | Default     | Notes                                       |
| ----------- | ----------- | ------------------------------------------- |
| `--port`    | `8765`      | Falls back to a free ephemeral port if busy |
| `--host`    | `127.0.0.1` | Set `0.0.0.0` to expose on your LAN         |
| `--no-open` | off         | Skip auto-opening the browser               |

Press `Ctrl+C` to stop. Both modes need only Python 3.10+ and a browser
with internet access for the CDN scripts.

## Quick start (agent)

Just ask Auggie / Claude / your IDE agent something like:

> Generate a deep wiki for `https://github.com/pallets/click` into
> `./docs/click-wiki/`.

The agent will discover this skill via `SKILL.md`, confirm prerequisites, and
invoke `scripts/generate_wiki.py` for you.

## Pipeline at a glance

1. `git clone --depth 1` into a temp workspace
2. Step 1 — repo metadata: `prompts/repo_metadata.txt` → `repo_metadata.json`
   (merged with GitHub REST + `git rev-parse HEAD`)
3. Step 2 — wiki structure: `prompts/wiki_structure.txt` → ≤10 sections
   (Overview + Architecture always present)
4. Step 3 — for each section: `prompts/wiki_section.txt` → `sections/<id>.mdx`
5. Assemble `wiki.mdx` (frontmatter, `# title`, "Last updated…" line, then
   `## section` blocks)
6. Optional MDX validation via `validate_mdx.mjs`

The orchestrator runs `auggie` once per step **with the same workspace and
cache directory**, so the codebase is indexed exactly once. See
[`references/architecture.md`](references/architecture.md) for the full
contract (prompt placeholders, retry policy, MDX rules).

## Updating the prompts

The prompts live in `prompts/*.txt` and are injected into `auggie` via
`--instruction-file`. They are formatted with `str.format` by the
orchestrator, so any literal `{` / `}` must be escaped as `{{` / `}}` in the
prompt. Supported placeholders per file:

| Prompt              | Placeholders                                        |
| ------------------- | --------------------------------------------------- |
| `repo_metadata.txt` | `{output_file}`                                     |
| `wiki_structure.txt`| `{output_file}`                                     |
| `wiki_section.txt`  | `{output_file}`, `{section_title}`, `{file_list}`   |

If you add a new placeholder, update the corresponding `generate_*` function
in `scripts/generate_wiki.py`.

## Troubleshooting

| Symptom                                     | Likely cause / fix                              |
| ------------------------------------------- | ----------------------------------------------- |
| `auggie binary not found`                   | Install Auggie or pass `--auggie-bin /path`     |
| `401 Unauthorized` from auggie              | Run `auggie login`; or export `AUGMENT_API_TOKEN`|
| `Step N produced no output`                 | Rerun with `--verbose --no-cleanup` and inspect |
|                                             | `<workspace>/__deepwiki_*` for partial output   |
| MDX validation skipped                      | `cd ~/.augment/skills/auggie-deep-wiki && npm i`|
| Mermaid diagram doesn't render in your CMS  | The CMS may need a remark/rehype plugin —       |
|                                             | the MDX itself compiles fine                    |

## License

Mirrors whatever license applies to `tools/deep-wiki/` in the upstream repo
this skill was extracted from.
