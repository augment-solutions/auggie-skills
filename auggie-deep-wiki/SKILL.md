---
name: auggie-deep-wiki
description: Generate a DeepWiki-style MDX repository guide (with Mermaid diagrams) by orchestrating the headless `auggie` CLI through three sequential steps — repo metadata, wiki structure, and per-section MDX. Use when the user asks to "generate a deep wiki", "create repository documentation", "write a DeepWiki-style guide", "scan a repo and produce an MDX wiki", "auto-document this codebase as MDX", or any equivalent ask that wants a multi-section MDX deliverable describing a repository's architecture and key modules. Triggers on phrases like "deep wiki", "auto-generate wiki", "MDX repo guide", "repository walkthrough as MDX", "DeepWiki for &lt;repo&gt;".
---

# auggie-deep-wiki

A self-contained Augment skill that drives the `auggie` CLI to produce a
DeepWiki-style guide for any Git repository. The orchestrator clones the repo
once, indexes it once, and then runs three sequential `auggie` invocations
that share the same workspace and cache directory:

1. **Repo metadata** — `prompts/repo_metadata.txt` → `repo_metadata.json`
2. **Wiki structure** — `prompts/wiki_structure.txt` → `wiki_structure.json`
   (≤10 sections, must include Overview + Architecture)
3. **Per-section MDX** — `prompts/wiki_section.txt` → `sections/<id>.mdx`
   for each section, then assembled into a single `wiki.mdx`.

The orchestrator is `scripts/generate_wiki.py`. It uses **only the Python
standard library** so the skill works without `pip install` / `uv sync`.

## Prerequisites

Before invoking the skill, make sure the user has:

- `auggie` on `$PATH` (or pass `--auggie-bin /path/to/auggie`).
- A valid Augment auth context. Either:
  - `~/.augment/.auggie.json` exists (created by `auggie login`), **or**
  - `AUGMENT_API_TOKEN` is exported.
- `git` on `$PATH`.
- Python 3.10+ (`python3 --version`).
- Optional, for MDX validation: Node.js + `@mdx-js/mdx`
  (`npm install -g @mdx-js/mdx`). Without it, validation is skipped with a
  warning rather than failing the run.

If any prerequisite is missing, tell the user and stop — do not attempt to
install software on their behalf without permission.

## When to use this skill

Use it when the user wants a **multi-section MDX wiki** for a repository.
Typical asks:

- "Generate a deep wiki for `https://github.com/pallets/flask`."
- "Write a DeepWiki-style guide for this repo into `./docs/wiki/`."
- "Auto-document the architecture of `<repo>` as MDX with Mermaid diagrams."
- "Run the deep-wiki generator on `<url>` and put it in `./out/`."

Do **not** use this skill for:

- Single-file READMEs (overkill — write the file directly).
- API docs / docstrings (different shape; use language-specific tooling).
- Anything that needs to push results to Sanity, GitHub Pages, or a CMS — the
  skill only writes local files; deployment is a separate step the user
  must approve.

## How to invoke

Run the orchestrator directly. There are no Python dependencies to install.

```bash
python3 ~/.augment/skills/auggie-deep-wiki/scripts/generate_wiki.py \
  https://github.com/pallets/flask \
  --output-dir ./output/flask \
  --model haiku4.5
```

Common flags:

- `--model` (`-m`): `haiku4.5` (default), `sonnet4`, `gemini25-pro`, …
- `--timeout` (`-t`): per-step timeout in seconds (default 3600).
- `--api-url`: defaults to `$AUGMENT_API_URL` or staging.
- `--workspace-dir` / `--cache-dir`: reuse existing dirs (skips re-clone /
  re-index across multiple runs of the same repo).
- `--no-cleanup`: keep the temp workspace + cache dirs after generation
  (useful for debugging an `auggie` failure).
- `--skip-validate`: skip the optional Node MDX validation pass.
- `--no-static`: skip auto-emitting `<output-dir>/index.html` (the
  self-contained browser viewer; emitted by default).
- `--verbose` (`-v`): debug logs.

`--help` lists all flags.

## Output layout

`--output-dir` is created if missing and populated with:

```
output_dir/
├── wiki.mdx               # Assembled, MDX-validated final guide
├── index.html             # Self-contained viewer (omit with --no-static)
├── wiki_structure.json    # Normalized TOC (id, title, importance, file_paths)
├── repo_metadata.json     # Merged LLM + GitHub API + git commit metadata
└── sections/
    ├── overview.mdx
    ├── architecture.mdx
    └── …                  # One MDX per section in wiki_structure.json
```

`index.html` embeds `wiki.mdx` inline as `<script type="text/markdown">` and
loads `marked` + `mermaid` from a CDN; it opens directly via `file://` and
can be packaged into any zip/tarball alongside the rest of the output.

`wiki.mdx` is a single MDX document with:
- Top-level `#` title
- "Last updated on … (Commit: …)" line linked to the GitHub commit when
  applicable
- One `##` heading per section followed by its generated MDX body
- Mermaid diagrams in fenced ` ```mermaid ` blocks (Overview + Architecture
  sections always include one)

## Operational notes for the agent

- **Authoritative reference**: see `references/architecture.md` for the
  detailed pipeline contract, prompt-format placeholders, and MDX rules
  enforced by `prompts/wiki_section.txt`. Read it before changing prompts
  or the orchestrator.
- **Determinism / cost**: each section is one `auggie` run. A 6-section wiki
  is ~6 LLM jobs plus metadata + structure (≈8 total). Warn the user if
  they target a very large repo with `sonnet4` — costs add up.
- **Retries**: the orchestrator retries `auggie` on `502/503/504/Bad
  Gateway/Unavailable` with exponential backoff (30s → 5m, max 3 attempts).
- **Failure modes**: a malformed `wiki_structure.json` aborts the run with a
  clear error. If `auggie` writes nothing to the expected output path the
  step also aborts; rerun with `--verbose --no-cleanup` to inspect the
  workspace.

## Previewing the output in a browser

There are two complementary ways to view a generated wiki, both rendering
Markdown via [`marked`](https://marked.js.org/) and fenced
` ```mermaid ` blocks via [`mermaid`](https://mermaid.js.org/) (CDN — no
`npm install` required for viewing).

**Static (no server, default).** Every successful run emits
`<output-dir>/index.html` with `wiki.mdx` inlined as a
`<script type="text/markdown">` block. Open it directly:

```bash
open ./output/flask/index.html        # macOS
xdg-open ./output/flask/index.html    # Linux
```

The whole `<output-dir>` is self-contained; zip/tar it and ship it. To
re-bundle an older output (or skip auto-emit with `--no-static` and
generate later), run:

```bash
python3 ~/.augment/skills/auggie-deep-wiki/scripts/build_static.py <output-dir>
```

**Live server.** `scripts/preview.py` serves the output dir over HTTP and
auto-opens the browser. If `<output-dir>/index.html` exists it serves that
(matching what the user gets via `file://`); otherwise it falls back to
the unbundled skill template that fetches `/wiki.mdx` dynamically.

```bash
python3 ~/.augment/skills/auggie-deep-wiki/scripts/preview.py ./output/flask
# → http://127.0.0.1:8765/  (auto-opens default browser)
```

Flags: `--port`, `--host`, `--no-open`. `Ctrl+C` to stop.

## Following up

After generation completes, suggest (don't auto-run) one or more of:

1. `open <output-dir>/index.html` to view the bundled static viewer.
2. Run `python3 ~/.augment/skills/auggie-deep-wiki/scripts/preview.py
   <output-dir>` for the live server (handy when iterating on prompts).
3. Open `wiki.mdx` directly to spot-check raw formatting.
4. Run `node ~/.augment/skills/auggie-deep-wiki/scripts/validate_mdx.mjs
   <wiki.mdx>` if `@mdx-js/mdx` is installed.
