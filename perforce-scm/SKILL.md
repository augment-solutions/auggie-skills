---
name: perforce-scm
description: >
  Use this skill whenever the agent needs to interact with a Perforce (Helix Core) source
  control system. Covers both the p4 CLI and P4Python SDK on Linux/macOS. Trigger this skill
  for any task involving: syncing/getting files from a Perforce depot, checking out or editing
  files, creating or submitting changelists, branching and merging (streams or classic
  branches), workspace/client setup, and user authentication. Also trigger when the user
  mentions p4, Perforce, Helix Core, depot paths (//depot/...), p4 clients/workspaces,
  changelists, streams, or p4 integrate/merge/copy. Do not skip this skill even for simple
  p4 operations — it contains critical patterns for error handling, authentication, and
  idempotent workspace management that prevent common agent failures.
compatibility: "Requires p4 CLI (Helix Command-Line Client) and/or P4Python (pip install p4python). Linux/macOS shell environment."
---

# Perforce (Helix Core) Skill

## 0. Detect the Perforce MCP Server (do this first)

Before falling back to the `p4` CLI or P4Python, **check whether the Perforce MCP
Server is available in the current agent session**. When present, it exposes
higher-level tools (`query_changelists`, `modify_changelists`, `query_reviews`,
`modify_reviews`, `query_files`, `modify_files`, `query_streams`, `modify_streams`,
`query_workspaces`, `modify_workspaces`, etc.) that should be **preferred over
raw CLI/SDK calls** because they handle authentication, error mapping, and
approval flows consistently.

**Detection heuristic** (run from agent context, in this order):

1. **Tool-list probe (most reliable)** — Inspect the available tool list for any
   tool name matching `query_changelists`, `modify_changelists`, `query_shelves`,
   `modify_shelves`, `query_files`, `modify_files`, `query_reviews`,
   `modify_reviews`, `query_streams`, `modify_streams`, `query_workspaces`, or
   `modify_workspaces`. If any of these are present, MCP is available —
   regardless of how the server was named.
2. **Server-name hint (advisory only)** — If your agent harness exposes MCP
   server metadata, look for a name matching the patterns `p4*mcp*` or
   `perforce*mcp*` (e.g., `p4mcp-server`, `perforce-p4-mcp`,
   `p4-mcp-server`). Because the server name is user-configured, this is
   informational only — always confirm with step 1.
3. Optionally check the environment variable `P4_MCP_ENDPOINT` if your harness
   sets one.

**Interface preference order** when more than one is available:

| Priority | Interface | When to use |
|---|---|---|
| 1 | **P4 MCP tools** | Always prefer when present — covers changelists, code review, file ops, stream workflows, workspace setup. |
| 2 | **P4Python SDK** | Python agent context without MCP, or when MCP doesn't cover the operation (e.g., low-level admin). |
| 3 | **`p4` CLI** | Shell scripts, one-off commands, or environments without Python/MCP. |

> **Do not duplicate** what the MCP already provides. If `modify_changelists`
> (create/submit/shelve), `modify_reviews`, `modify_files` (edit/add/delete/sync),
> `modify_streams`, or `modify_workspaces` is available, use it instead of
> shelling out to `p4`. Fall back to CLI/SDK only for capabilities the MCP does
> not expose, or when the MCP is absent.

See `references/mcp-integration.md` for the full MCP tool catalog and a mapping
of each MCP skill area to the corresponding sections of this document.

---

## Interface Selection (fallback when MCP is unavailable)

| Situation | Use |
|---|---|
| Simple one-off commands, shell scripts | **p4 CLI** |
| Python agents, structured data, error handling | **P4Python SDK** |
| Either interface is acceptable | Prefer **P4Python** in a Python context for richer error handling. |

---

## 1. Authentication & Connection

### Environment Variables (always set these first)
```bash
export P4PORT=ssl:your-server:1666   # or tcp:host:1666 for non-SSL
export P4USER=your_username
export P4CLIENT=your_workspace_name
export P4PASSWD=your_ticket_or_password   # or use p4 login
```

### CLI: Login
```bash
# Password login (interactive)
p4 login

# Non-interactive (pipe password)
echo "$P4PASSWD" | p4 login

# Verify login status
p4 login -s

# Use ticket file (preferred for agents/CI)
p4 login -a   # all hosts; writes to ~/.p4tickets
```

### P4Python: Connect & Login
```python
from P4 import P4, P4Exception

p4 = P4()
p4.port    = "ssl:your-server:1666"
p4.user    = "your_username"
p4.client  = "your_workspace_name"
p4.password = "your_password_or_ticket"

try:
    p4.connect()
    p4.run_login()
    # ... do work ...
finally:
    p4.disconnect()
```

> **Tip:** Prefer ticket-based auth. After a successful `p4.run_login()`, the ticket is
> cached in `~/.p4tickets` and reused automatically on subsequent connects without
> re-supplying the password.

### P4Python: Context Manager Pattern (recommended)
```python
from P4 import P4, P4Exception

with P4() as p4:
    p4.port   = "ssl:your-server:1666"
    p4.user   = "agent_user"
    p4.client = "agent_workspace"
    p4.connect()
    p4.run_login()
    results = p4.run_sync("//depot/project/...")
```

---

## 2. Workspace / Client Setup

A **client** (workspace) maps depot paths to local disk paths.

### CLI: Create or update a client spec
```bash
# Fetch the current client spec (or a template if new)
p4 client -o my_workspace_name

# Edit and save the spec non-interactively using a here-doc
p4 client -i << 'EOF'
Client: my_workspace_name
Owner:  agent_user
Root:   /home/agent/workspace
Options: noallwrite noclobber nocompress unlocked nomodtime normdir
SubmitOptions: submitunchanged
LineEnd: local
View:
    //depot/project/... //my_workspace_name/project/...
EOF

# Confirm workspace exists
p4 clients -u agent_user
```

### P4Python: Create/update a client
```python
client_spec = p4.fetch_client("my_workspace_name")
client_spec["Root"]    = "/home/agent/workspace"
client_spec["Options"] = "noallwrite noclobber nocompress unlocked nomodtime normdir"
client_spec["View"]    = ["//depot/project/... //my_workspace_name/project/..."]
p4.save_client(client_spec)
p4.client = "my_workspace_name"
```

### Delete a workspace (cleanup)
```bash
p4 client -d my_workspace_name   # CLI
```
```python
p4.delete_client("my_workspace_name")   # P4Python
```

> **Idempotency:** Always check if a client exists before creating it. Use
> `p4 clients -e workspace_name` (CLI) or `p4.fetch_client(name)` (P4Python, catches
> P4Exception if not found) to guard against duplicate creation.

---

## 3. Syncing / Getting Files

### CLI
```bash
# Sync all files in client view to head revision
p4 sync

# Sync a specific path
p4 sync //depot/project/...

# Sync to a specific changelist
p4 sync //depot/project/...@123456

# Sync to a label
p4 sync //depot/project/...@release-label

# Force sync (re-fetch even if already at head)
p4 sync -f //depot/project/...

# Preview what would sync (no file changes)
p4 sync -n //depot/project/...

# Flush (update have table without writing files — useful for CI)
p4 sync -k //depot/project/...
```

### P4Python
```python
# Sync to head
results = p4.run_sync("//depot/project/...")

# Sync to changelist
results = p4.run_sync("//depot/project/...@123456")

# Force sync
results = p4.run_sync("-f", "//depot/project/...")

# Check what would sync
results = p4.run_sync("-n", "//depot/project/...")

# results is a list of dicts, e.g.:
# [{'depotFile': '//depot/project/main.c', 'action': 'updated', ...}]
for r in results:
    print(r.get("depotFile"), r.get("action"))
```

---

## 4. Checkout & Submit (Changelists)

### Typical Edit → Submit Workflow

#### CLI
```bash
# 1. Open file(s) for edit
p4 edit //depot/project/src/main.c

# 2. Make changes on disk
# ... edit /home/agent/workspace/project/src/main.c ...

# 3. Create a named changelist
CL=$(p4 change -o | sed 's/<enter description here>/Fix bug 1234/' | p4 change -i | grep -E '^Change [0-9]+ created' | awk '{print $2}')

# 4. Move opened files into the changelist
p4 reopen -c $CL //depot/project/src/main.c

# 5. Submit
p4 submit -c $CL
```

#### P4Python (preferred for agents)
```python
# 1. Open for edit
p4.run_edit("//depot/project/src/main.c")

# 2. Make changes on disk
with open("/home/agent/workspace/project/src/main.c", "a") as f:
    f.write("\n// Fix applied by agent\n")

# 3. Create changelist
change = p4.fetch_change()
change["Description"] = "Fix bug 1234 - applied by agent"
change["Files"] = ["//depot/project/src/main.c"]

# 4. Submit
result = p4.run_submit(change)
submitted_cl = result[-1].get("submittedChange")
print(f"Submitted changelist: {submitted_cl}")
```

### Add New Files
```bash
p4 add //depot/project/new_file.txt    # CLI
```
```python
p4.run_add("//depot/project/new_file.txt")  # P4Python
# Or add by local path:
p4.run_add("/home/agent/workspace/project/new_file.txt")
```

### Delete Files
```bash
p4 delete //depot/project/old_file.txt
```
```python
p4.run_delete("//depot/project/old_file.txt")
```

### Revert (discard changes)
```bash
p4 revert //depot/project/src/main.c   # revert specific file
p4 revert -a                           # revert all unchanged files
```
```python
p4.run_revert("//depot/project/src/main.c")
p4.run_revert("-a", "//depot/...")    # revert unchanged only
```

### Query Open Files / Pending Changelists
```bash
p4 opened              # all files open in client
p4 changes -s pending  # all pending changelists
p4 describe -s 123456  # describe a changelist
```
```python
opened = p4.run_opened()
pending = p4.run_changes("-s", "pending")
desc = p4.run_describe("-s", "123456")
```

---

## 5. Branching & Merging

> **Key concept:** For Streams-based repos, use `p4 merge` (merge down) and `p4 copy`
> (copy up). For classic branch specs, use `p4 integrate`.
> See `references/branching.md` for full details.

### Streams: Merge Down (child gets parent changes)
```bash
# Switch workspace to the child stream
p4 client -s -S //depot/streams/dev

# Merge from parent (mainline → dev)
p4 merge --from //depot/streams/main

# Resolve (accept theirs, accept yours, or interactive)
p4 resolve -am   # auto-merge
p4 resolve -as   # accept source (theirs)
p4 resolve -at   # accept target (yours)

# Submit the merge changelist
p4 submit -d "Merge main into dev"
```

### Streams: Copy Up (promote dev changes to mainline)
```bash
p4 client -s -S //depot/streams/main  # switch to main stream client
p4 copy --from //depot/streams/dev
p4 resolve -as
p4 submit -d "Promote dev to main"
```

### Classic Branches: Integrate
```bash
# Integrate (merge) from source branch to target
p4 integrate //depot/main/... //depot/release/1.0/...

# Resolve conflicts
p4 resolve -am

# Submit
p4 submit -d "Integrate main into release/1.0"
```

### P4Python: Merge & Resolve
```python
# Merge down from parent stream
p4.run_merge("--from", "//depot/streams/main")

# Auto-resolve
p4.run_resolve("-am")

# Submit
change = p4.fetch_change()
change["Description"] = "Merge main into dev"
p4.run_submit(change)
```

---

## 6. Error Handling

### P4Python Exception Pattern
```python
from P4 import P4, P4Exception

p4 = P4()
p4.exception_level = 1   # Raise on errors only (not warnings)

try:
    p4.connect()
    p4.run_edit("//depot/project/file.c")
except P4Exception as e:
    for err in p4.errors:
        print(f"ERROR: {err}")
    for warn in p4.warnings:
        print(f"WARNING: {warn}")
    raise
finally:
    p4.disconnect()
```

### Common Errors & Fixes

| Error | Cause | Fix |
|---|---|---|
| `Connect to server failed` | Wrong P4PORT or server down | Check P4PORT; verify connectivity with `p4 info` |
| `Client does not exist` | Wrong P4CLIENT | Create workspace or fix `p4.client` |
| `File(s) not on client` | File outside workspace view | Expand View in client spec |
| `... already opened for edit` | File already checked out | Use `p4 revert` or continue with existing checkout |
| `... out of date` on submit | Another user submitted first | Run `p4 sync`, re-edit, re-submit |
| `Merges still pending` | Unresolved files after merge | Run `p4 resolve` before submitting |
| `Authentication ticket has expired` | Old ticket | Run `p4 login` again |

---

## 7. Useful Diagnostic Commands

```bash
p4 info                    # server/client info; confirm connection
p4 where //depot/path/...  # show local path mapping
p4 have //depot/path/...   # show synced revisions
p4 filelog //depot/file.c  # history of a file
p4 diff //depot/file.c     # diff workspace vs depot
p4 changes -m 10           # last 10 submitted changelists
p4 streams                 # list all streams (streams depot)
p4 clients -u username     # list workspaces for a user
```

```python
# P4Python equivalents
p4.run_info()
p4.run_where("//depot/path/...")
p4.run_have("//depot/path/...")
p4.run_filelog("//depot/file.c")
p4.run_diff("//depot/file.c")
p4.run_changes("-m", "10")
p4.run_streams()
```

---

## 8. Best Practices for Agents

1. **Always disconnect:** Use `try/finally` or the context manager; leaked connections
   consume server licenses.
2. **Use dedicated workspaces:** Give the agent its own uniquely-named client (e.g.,
   `agent-hostname-task`) to avoid colliding with human workspaces.
3. **Revert before re-syncing:** Run `p4 revert -a //...` at the start of each task to
   clear any leftover open files from a previous run.
4. **Check `p4.warnings`:** P4Python puts non-fatal server messages in `p4.warnings`, not
   exceptions. Always check them after critical operations.
5. **Use changelist numbers:** Never rely on the "default" changelist in automated scripts;
   always create a numbered changelist so it can be identified and reverted if needed.
6. **Prefer depot paths (`//depot/...`)** over local paths in commands — they are
   workspace-agnostic.
7. **Set `p4.exception_level = 1`** so warnings don't raise exceptions but errors do.

---

## Reference Files

- `references/branching.md` — Deep dive on Streams vs. classic branch workflows,
  merge-down/copy-up patterns, and conflict resolution strategies. Read this for complex
  branching tasks.
- `references/mcp-integration.md` — Catalog of the Perforce MCP Server's bundled
  skills and tools, with a mapping showing which sections of this skill are
  superseded by MCP tools when the MCP is available.
