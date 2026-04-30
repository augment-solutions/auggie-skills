# auggie-deep-wiki — pipeline reference

This file is loaded **on demand**: open it before modifying prompts or the
orchestrator, or when debugging unexpected `auggie` output.

## Pipeline contract

```
        ┌────────────────┐
        │  git clone     │  --depth 1 --single-branch
        └───────┬────────┘
                │
                ▼   workspace_dir + cache_dir (reused across all auggie runs)
   ┌───────────────────────────────────────────────────────┐
   │ Step 1: repo_metadata                                  │
   │   prompts/repo_metadata.txt → __deepwiki_repo_metadata │
   │   .json (in workspace_dir)                             │
   │   merged with GitHub REST API + git rev-parse HEAD     │
   │   → output_dir/repo_metadata.json                      │
   └───────────────────────────────────────────────────────┘
                │
                ▼
   ┌───────────────────────────────────────────────────────┐
   │ Step 2: wiki_structure                                 │
   │   prompts/wiki_structure.txt → JSON with ≤10 sections  │
   │   normalized: stable id slugs, importance ∈            │
   │   {high, medium, low}, file_paths list                 │
   │   Overview + Architecture inserted if missing          │
   │   → output_dir/wiki_structure.json                     │
   └───────────────────────────────────────────────────────┘
                │
                ▼   for each section in structure:
   ┌───────────────────────────────────────────────────────┐
   │ Step 3: wiki_section (one auggie run per section)      │
   │   prompts/wiki_section.txt → MDX body                  │
   │   → output_dir/sections/<id>.mdx                       │
   └───────────────────────────────────────────────────────┘
                │
                ▼
   ┌───────────────────────────────────────────────────────┐
   │ Assembly                                               │
   │   "---" frontmatter + "# title"                        │
   │   "Last updated on <date> (Commit: <link>)"            │
   │   "## title" + body for each section                   │
   │   trailing "---"                                       │
   │   → output_dir/wiki.mdx                                │
   │   optional MDX validation via Node + @mdx-js/mdx       │
   └───────────────────────────────────────────────────────┘
```

## Prompt placeholders

`scripts/generate_wiki.py` calls `str.format` on each prompt template. Any
literal `{` / `}` in the prompts is escaped as `{{` / `}}` (already done in
the bundled prompts). The supported placeholders are:

| Prompt              | Placeholders supplied by the orchestrator           |
| ------------------- | --------------------------------------------------- |
| `repo_metadata.txt` | `{output_file}`                                     |
| `wiki_structure.txt`| `{output_file}`                                     |
| `wiki_section.txt`  | `{output_file}`, `{section_title}`, `{file_list}`   |

`file_list` is a Markdown bullet list of the section's `file_paths`, or a
fallback message when the structure listed none.

If you add new placeholders to a prompt, update the corresponding
`generate_*` function in `scripts/generate_wiki.py` to pass it.

## Auggie command shape

```
auggie \
  --workspace-root <workspace_dir> \
  --instruction-file <tempfile with the formatted prompt> \
  --augment-cache-dir <cache_dir> \
  --model <model> \
  --print \
  --allow-indexing \
  [--augment-session-json ~/.augment/.auggie.json]   # only when the file exists
```

`AUGMENT_API_URL` is exported into the child env (default
`https://staging-shard-0.api.augmentcode.com`). `AUGMENT_API_TOKEN`, if set
in the parent env, is inherited automatically.

## MDX rules enforced by `prompts/wiki_section.txt`

These are repeated to the LLM verbatim and are also the rules the optional
`@mdx-js/mdx` validator enforces:

- Block-level JSX (`<details>`, `<div>`, `<summary>`) must start at column 0
  and have a blank line before and after.
- All JSX must be fully closed; `<Component />` for self-closing.
- No HTML comments — use `{/* … */}` if needed.
- Escape `<` and `>` in prose as `&lt;` / `&gt;`; escape `{`/`}` either with
  backticks or `\{` / `\}`. (Inside fenced code blocks no escaping needed.)
- Use `* * *` instead of `---` for horizontal rules; `---` is reserved for
  frontmatter.
- Mermaid diagrams use ` ```mermaid ` fences, `flowchart TD`/`LR`, double-
  quoted labels, alphanumeric IDs, ≤15 nodes.
- Overview sections include a high-level system view; Architecture sections
  include data flows or dependency graphs.

When changing these rules, update both `prompts/wiki_section.txt` (the LLM's
instructions) and any consumers of `wiki.mdx` to keep them in sync.

## Retry / cleanup behavior

- `auggie` is retried up to 3 times on transient errors containing
  `502`, `503`, `504`, `bad gateway`, or `unavailable`. Backoff: 30s → 60s
  → 120s (capped at 300s).
- Workspace and cache temp dirs are removed at the end of a run unless
  `--no-cleanup` is set, or unless the user passed an explicit
  `--workspace-dir` / `--cache-dir` (in which case the orchestrator never
  deletes user-supplied paths).
- A `KeyboardInterrupt` exits with code 130; any other failure exits 1
  (or 2 if `git`/`auggie` are missing).
