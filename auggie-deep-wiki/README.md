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
   temp directory. The script auto-detects which credential source to
   use and logs the chosen mode at the start of the run (see
   "Authentication" below). When the initial clone fails with a
   401/403/404 **and** the script was running in
   `http-authorization-header` mode (i.e. a token was injected via
   `-c http.extraHeader=…`), it retries once without the header so a
   public host repo still works when the env-var token doesn't cover
   it. The retry is intentionally not attempted in `git-credential-helper`
   mode (the helper owns credential lifecycle and we cannot suppress
   it from a single invocation) or in `ssh-key` / `anonymous` modes
   (no header to drop).
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

`publish_git.py` picks one of four modes at the start of every run
and logs the choice on a single line whose message contains the
literal token `Auth: <mode> -` (followed by a one-sentence,
human-readable explanation). The full emitted line is prefixed by
the standard `logging` timestamp/level/logger preamble, so a
substring match like `Auth: ([\w-]+) -` (not anchored at start of
line) is the right way for tooling to extract the mode. The
`<mode>` token is one of `git-credential-helper`,
`http-authorization-header`, `ssh-key`, or `anonymous`, and is
stable across releases. Example log messages (preamble omitted):

```text
Auth: git-credential-helper - deferring to git credential helper for https://github.com/... (env GITHUB_TOKEN/GH_TOKEN ignored to keep helper-managed token fresh).
Auth: http-authorization-header - HTTP Authorization header from env (no credential helper configured for this host; token will not be auto-refreshed for this run).
Auth: ssh-key - ssh-agent / private key supplies the credential (transport is scp-like); $GITHUB_TOKEN is ignored. A permission failure here points at the key, the agent, or the remote `authorized_keys` / deploy-key configuration.
Auth: anonymous - no credential helper, no GITHUB_TOKEN/GH_TOKEN. Push and private-repo clone will fail; only public-repo clone works.
```

| Mode                          | When                                                         | Notes |
| ----------------------------- | ------------------------------------------------------------ | ----- |
| `git-credential-helper`       | HTTPS URL and a git credential helper is configured for the host (Cosmos sandbox, `gh auth login`) | Preferred. Helper-managed tokens stay fresh and survive 401 via `erase`→refresh. Env vars are intentionally ignored to avoid pinning a stale token. |
| `http-authorization-header`   | HTTPS URL, no helper configured, but `GITHUB_TOKEN` (or `GH_TOKEN`) is set | Fallback. Token injected via `-c http.extraHeader=…` for that single invocation — never written to `.git/config` or logs. No auto-refresh; very long runs may need a re-publish if the env var expires. |
| `ssh-key`                     | URL is an SSH transport (`git@host:org/repo.git` or `ssh://...`) | ssh-agent / private key supplies the credential. `$GITHUB_TOKEN` is ignored. A failure here points at the key, the agent, or the remote `authorized_keys` / deploy-key configuration. |
| `anonymous`                   | HTTPS URL, no helper, no token                               | Public-repo clone works; push and private-repo clone fail with an actionable error. |

| Environment            | Typical mode                          | What to set                                              |
| ---------------------- | ------------------------------------- | -------------------------------------------------------- |
| Local (HTTPS clone)    | `git-credential-helper` (preferred)   | `gh auth login`, or set `GITHUB_TOKEN` (PAT with `contents: write`) for header-mode fallback |
| Local (SSH clone)      | `ssh-key`                             | Use `git@github.com:<org>/deep-wikis.git` and a configured key (the script logs `Auth: ssh-key - …`) |
| Poseidon / Cosmos      | `git-credential-helper` (preferred)   | Just set `DEEP_WIKIS_GIT_REPO`. The sandbox installs the helper at boot and refreshes the installation token on demand. |
| GitHub Actions         | `http-authorization-header`           | `GITHUB_TOKEN` is provided by the runner; just set `DEEP_WIKIS_GIT_REPO` |

The skill never persists or echoes the token.

#### Push failure classification

Auth-class push failures are tagged with one of `auth-401`, `auth-403`,
`auth-404`, or `auth-no-credential` and surfaced with a one-paragraph
remediation hint. An agent reading the log can immediately tell
whether the user needs to refresh a token, edit GitHub App
permissions, add the host repo to the App's selected repositories, or
configure a credential at all.

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
| `git push failed (auth-401): …`             | Credential rejected as invalid/expired. On Cosmos: check the GitHub App is installed on the host repo's owner. Locally: `gh auth refresh` or regenerate `$GITHUB_TOKEN` |
| `git push failed (auth-403): …`             | Credential authenticated but lacks write access. GitHub App: edit permissions to include `Contents: Read & write`. PAT: needs `repo` scope (classic) or `Contents: write` (fine-grained) |
| `git push failed (auth-404): …`             | Repo not in credential's scope. GitHub App: add the host repo to the App's selected repositories on github.com |
| `git push failed (auth-no-credential): …`   | No credential available. Configure a credential helper (`gh auth login`) or set `GITHUB_TOKEN` |
| Push rejected, retry message in logs        | Concurrent publish from another session — the script rebases and retries up to 3 times automatically; if it still fails, rerun |
| Wiki appears at the wrong URL               | Override the slug with `--wiki-slug <slug>` (default is `<owner>-<repo>`) |
| Want to preview before pushing              | Use `--no-push --keep-wiki-work-dir --wiki-work-dir /tmp/deep-wikis-clone` and inspect the commit |

## License

Mirrors whatever license applies to `tools/deep-wiki/` in the upstream repo
this skill was extracted from.
