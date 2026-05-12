# Perforce MCP Server Integration

When the [Perforce MCP Server](https://github.com/perforce/p4mcp-server) is
attached to the agent session, **prefer its tools over the `p4` CLI and
P4Python**. This file catalogs the MCP-bundled skills, the tools each exposes,
and which sections of the parent `SKILL.md` they supersede so the agent does
not duplicate work.

## Detection

The MCP exposes tools using a `query_<noun>` / `modify_<noun>` naming pattern.
Concrete tool names you can probe for:

- `query_changelists`, `modify_changelists`
- `query_shelves`,    `modify_shelves`
- `query_reviews`,    `modify_reviews`
- `query_files`,      `modify_files`
- `query_streams`,    `modify_streams`
- `query_workspaces`, `modify_workspaces`

If **any** of the above are present in the session's tool list, treat MCP as
available and route operations through it.

## Bundled MCP Skills → `perforce-scm` Section Mapping

| MCP Skill | MCP Tools | Supersedes `SKILL.md` Section | Fall back to CLI/SDK when… |
|---|---|---|---|
| `p4-workspace-setup` | `query_workspaces`, `modify_workspaces` | §2 Workspace / Client Setup | You need uncommon client options not exposed by the tool. |
| `p4-file-operations` | `query_files`, `modify_files` | §3 Syncing, §4 Checkout (edit/add/delete/revert) | You need force-flush (`p4 sync -k`) or rare flag combos. |
| `p4-changelist-management` | `query_changelists`, `modify_changelists`, `query_shelves`, `modify_shelves` | §4 Changelist creation & submit, shelving | Operations on submitted changelists' metadata. |
| `p4-stream-workflows` | `query_streams`, `modify_streams` | §5 Streams: merge-down / copy-up | Classic (non-stream) `p4 integrate`. |
| `p4-code-review` | `query_reviews`, `modify_reviews` | *(not covered by `perforce-scm`)* — Swarm code review | n/a — MCP is the only interface. |

## Tools Exclusive to MCP (no `perforce-scm` equivalent)

- **Swarm code review** (`query_reviews`, `modify_reviews`) — create reviews,
  vote, comment, transition states, manage participants.
- **Shelve diff / files** (`query_shelves` → `diff`, `files`) — server-side
  inspection of shelved content without unshelving.
- **Review dashboard** (`query_reviews` → `dashboard`) — list reviews assigned
  to the current user.

For these, **always use MCP**; this skill provides no equivalent.

## Capabilities Still Owned by `perforce-scm`

Even when MCP is present, fall back to `p4` CLI / P4Python for:

- Low-level **authentication & ticket management** (`p4 login -a`, `~/.p4tickets`)
  — MCP assumes a working connection.
- **Classic branch `integrate`** for non-stream depots (`p4 integrate -b
  branch_spec`) — MCP focuses on streams.
- **Advanced conflict resolution edge cases** — `modify_files` → `resolve`
  supports `auto`, `theirs`, `yours`, and `preview` modes; use the CLI
  (`p4 resolve -at` / `-ay` / interactive) only when you need a resolve strategy
  the MCP tool does not expose (e.g., programmatic three-way merge with custom
  merge driver).
- **Diagnostics & auditing**: `p4 info`, `p4 monitor show`, `p4 -ztag`,
  `p4 changes -m N`, `p4 filelog`.
- **Workspace cleanup at scale** (delete many stale clients, batch reverts).

## Routing Decision (pseudo-logic)

```text
if task in {create/submit/move/list changelists, shelve, unshelve}:
    use modify_changelists / modify_shelves / query_*
elif task in {sync, edit, add, delete, revert, diff files}:
    use modify_files / query_files
elif task in {create/update/delete workspace, list workspaces}:
    use modify_workspaces / query_workspaces
elif task involves a stream (merge-down, copy-up, list streams):
    use modify_streams / query_streams
elif task is a code review (Swarm):
    use modify_reviews / query_reviews
else:
    fall back to p4 CLI or P4Python per the Interface Selection table
```

## Anti-Duplication Rules

1. **Never** wrap `p4 submit` when `modify_changelists` → `submit` is available.
2. **Never** parse `p4 -ztag changes` output when `query_changelists` → `list`
   returns structured data.
3. **Never** manually craft a client spec via here-doc when `modify_workspaces`
   → `create`/`update` is available.
4. **Never** implement Swarm review automation through HTTP/CLI — always use
   `modify_reviews`.
5. If MCP returns "not supported" or a capability gap, **then** drop down to
   P4Python; use the CLI only as a last resort.

## References

- Upstream MCP server: <https://github.com/perforce/p4mcp-server>
- Bundled skills: `skills/p4-changelist-management`, `skills/p4-code-review`,
  `skills/p4-file-operations`, `skills/p4-stream-workflows`,
  `skills/p4-workspace-setup`
