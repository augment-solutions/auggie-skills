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
│   ├── publish_git.py             # Optional: publish to a Git-backed Astro repo
│   └── validate_mdx.mjs           # Optional Node MDX validator
├── preview/
│   └── index.html                 # Viewer template (marked + mermaid via CDN)
├── astro-template/                # Reference Astro site for bootstrapping a host repo
│   ├── package.json
│   ├── astro.config.mjs
│   └── src/
│       ├── content.config.ts      # `wikis` collection (one entry per published wiki)
│       ├── layouts/WikiLayout.astro
│       └── pages/                 # /, /wikis/[...slug]/
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

## Publishing to a Git-backed Astro site (optional)

By default the skill writes only to the local filesystem. Pass
`--publish-git` (or ask the agent to "publish this wiki") to also
push the result into a host Astro repository. Any static-site host
that auto-deploys on push (Vercel, Netlify, Cloudflare Pages, GitHub
Pages, etc.) will then pick up the new wiki without further action.

```bash
export DEEP_WIKIS_GIT_REPO=https://github.com/<org>/deep-wikis.git
python3 scripts/generate_wiki.py \
  https://github.com/pallets/click \
  --output-dir ./output/click \
  --publish-git
```

What happens:

1. `git clone --depth=1 --branch <branch> $DEEP_WIKIS_GIT_REPO` into a
   temp directory. If `GITHUB_TOKEN` (or `GH_TOKEN`) is set, it is
   passed via an `Authorization: Bearer …` header for that single
   invocation — never written to `.git/config` or logs.
2. `wiki.mdx` + `repo_metadata.json` are converted into an Astro
   content-collection entry at
   `src/content/wikis/<slug>/index.mdx`. Any existing directory for
   the same slug is replaced atomically so stale auxiliary files from
   a previous run don't linger.
3. The change is committed with the message
   `deep-wiki: update <slug>` and pushed to `origin <branch>`. If
   the push is rejected because another session pushed concurrently,
   the script `pull --rebase`s and retries up to 3 times.
4. The host's CI/CD (Vercel auto-deploy on push, GitHub Pages action,
   etc.) rebuilds the unified site.

### Setting up the host repository (one-time)

The skill ships **no default host repo** so each team can self-host.
To bootstrap your own:

1. Create a new GitHub (or GitLab/Bitbucket) repo, e.g.
   `<org>/deep-wikis`.
2. Copy `astro-template/` from this skill into the empty repo:
   ```bash
   cp -R ~/.augment/skills/auggie-deep-wiki/astro-template/* /path/to/cloned-host-repo/
   cd /path/to/cloned-host-repo
   git add -A && git commit -m "Initial Astro scaffold"
   git push origin main
   ```
3. Wire it to your static-site host. For Vercel: create a project
   from the repo on <https://vercel.com/new> — Astro is auto-detected
   and no environment variables are required.
4. Export the URL so the skill can find it:
   ```bash
   echo 'export DEEP_WIKIS_GIT_REPO=https://github.com/<org>/deep-wikis.git' >> ~/.zshrc
   ```

### Authentication

| Environment            | How to authenticate                                  |
| ---------------------- | ---------------------------------------------------- |
| Local (HTTPS clone)    | Set `GITHUB_TOKEN` (PAT with `contents: write`), or rely on git's credential helper / OS keychain |
| Local (SSH clone)      | Use `git@github.com:<org>/deep-wikis.git` and the SSH key already configured for git |
| Poseidon / CI sandbox  | `GITHUB_TOKEN` is typically already exported; just set `DEEP_WIKIS_GIT_REPO` |

The skill never persists or echoes the token.

### Hosting multiple wikis on one site

This is the default and only mode:

- Every published wiki becomes `src/content/wikis/<slug>/index.mdx`
  in the host repo. The same slug is replaced in-place on re-publish;
  every other wiki is left untouched.
- The slug is derived from the cloned repo (`<owner>-<repo>`).
  Override with `--wiki-slug` for a custom path.
- Astro's content collection picks every entry up at build time
  and produces `/`, `/wikis/<slug>/` routes — one static page per
  wiki plus a landing page that lists them all.

Inspect or remove a published wiki by editing the host repo directly:

```bash
git clone https://github.com/<org>/deep-wikis.git
cd deep-wikis
ls src/content/wikis/
# pallets-click  pallets-flask  some-other-repo
git rm -r src/content/wikis/some-other-repo
git commit -m "deep-wiki: remove some-other-repo" && git push
```

### Manual publish (without re-running auggie)

`publish_git.py` can be invoked directly against an existing output
directory:

```bash
python3 scripts/publish_git.py \
  --output-dir ./output/click \
  --wiki-repo https://github.com/<org>/deep-wikis.git \
  --slug pallets-click
```

Use `--no-push` to commit into the temp clone but stop before pushing
(handy for inspecting the generated MDX), or `--keep-work-dir
--work-dir /tmp/deep-wikis-clone` to preserve the clone for review.

The publish step also runs `npm install` (when needed) + `npm run
build` against the cloned host repo before pushing. That pre-flight
catches malformed MDX/YAML frontmatter so the same error you'd see
on the deploy provider (Vercel, Netlify, …) is surfaced locally —
no commit, no push, host repo stays green. If `node`/`npm` aren't on
your `$PATH`, the publish step skips and prints a recovery summary
instead of pushing. Pass `--skip-build-validation` to bypass the
check (use only when CI re-runs the same `astro build`).



## Quick start (agent)

Just ask Auggie / Claude / your IDE agent something like:

> Generate a deep wiki for `https://github.com/pallets/click` into
> `./docs/click-wiki/`.

The agent will discover this skill via `SKILL.md`, confirm prerequisites, and
invoke `scripts/generate_wiki.py` for you.

To also publish the result to your team's site, mention it explicitly:

> Generate a deep wiki for `https://github.com/pallets/click` and
> **publish it to our deep-wikis site**.

The agent passes `--publish-git` to the orchestrator (with
`$DEEP_WIKIS_GIT_REPO` already exported in your environment), which
runs the default static output first and then the optional publish
pipeline.

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
| `No host repo configured`                   | Pass `--wiki-repo …` or export `DEEP_WIKIS_GIT_REPO` |
| `git push failed: Authentication failed`    | Set `GITHUB_TOKEN` (PAT with `contents: write`) for HTTPS, or use the SSH form of the URL with a configured key |
| Push rejected, retry message in logs        | Concurrent publish from another session — the script rebases and retries up to 3 times automatically; if it still fails, rerun |
| Wiki appears at the wrong URL               | Override the slug with `--wiki-slug <slug>` (default is `<owner>-<repo>`) |
| Want to preview before pushing              | Use `--no-push --keep-wiki-work-dir --wiki-work-dir /tmp/deep-wikis-clone` and inspect the commit |

## License

Mirrors whatever license applies to `tools/deep-wiki/` in the upstream repo
this skill was extracted from.
