# Perforce (Helix Core) Skill

A Claude agent skill for interacting with Perforce source control. Covers the **p4 CLI** and **P4Python SDK** on Linux/macOS, with ready-to-use patterns for the most common depot workflows.

---

## What This Skill Covers

| Workflow | CLI | P4Python |
|---|:---:|:---:|
| Authentication & ticket login | ✅ | ✅ |
| Workspace / client setup | ✅ | ✅ |
| Syncing files (head, CL, label) | ✅ | ✅ |
| Checkout, edit, add, delete | ✅ | ✅ |
| Changelist creation & submit | ✅ | ✅ |
| Streams: merge-down / copy-up | ✅ | ✅ |
| Classic branch integrate | ✅ | ✅ |
| Conflict resolution strategies | ✅ | ✅ |
| Diagnostics & auditing | ✅ | ✅ |

A companion reference file (`references/branching.md`) covers advanced branching topics — stream hierarchies, cherry-picking, merge history, and common pitfalls — and is loaded by the agent only when needed.

---

## Requirements

- **p4 CLI** — [Download Helix Command-Line Client](https://www.perforce.com/downloads/helix-command-line-client-p4)
- **P4Python** (optional, for Python agents) — `pip install p4python`
- Python 3.9+ if using P4Python (see [compatibility matrix](https://github.com/perforce/p4python/blob/master/RELNOTES.txt))
- Linux or macOS environment

---

## Installation

### Claude.ai Projects
1. Go to your Project → **Skills** tab
2. Upload `perforce.skill`
3. The skill auto-activates whenever your agent detects a Perforce-related task

### Custom Agent / Claude Code
Place the unpacked skill folder in your skills directory and reference it in your agent configuration:
```
skills/
└── perforce-scm/
    ├── SKILL.md
    └── references/
        └── branching.md
```

---

## How to Use With Agents

### Triggering the Skill
The skill activates automatically when your prompt mentions any of the following:

- `p4`, `Perforce`, `Helix Core`
- Depot paths like `//depot/...` or `//streams/...`
- Terms: `changelist`, `workspace`, `client spec`, `p4 sync`, `p4 submit`, `p4 integrate`, `p4 merge`

You don't need to explicitly invoke it — just describe what you want to do in Perforce terms.

### Example Agent Prompts

**Sync and build:**
```
Sync //depot/myproject/... to changelist 98432, then run the build script.
```

**Automated code submission:**
```
Check out //depot/tools/config.json, update the version field to 2.4.1,
and submit it with the description "Bump version to 2.4.1".
```

**Branch integration:**
```
Merge all pending changes from //depot/streams/main into
//depot/streams/dev, auto-resolve any conflicts, and submit.
```

**Workspace bootstrap:**
```
Create a Perforce workspace named "agent-build-01" mapped to
//depot/project/... rooted at /home/ci/workspace, then sync to head.
```

### Setting Up Credentials

The agent expects these environment variables to be set before it runs. Set them in your shell, CI secrets, or agent config:

```bash
export P4PORT=ssl:your-perforce-server:1666
export P4USER=your_username
export P4CLIENT=your_workspace_name
export P4PASSWD=your_ticket_or_password   # or use p4 login beforehand
```

For CI pipelines, log in once and let the agent use the cached ticket:
```bash
echo "$P4PASSWD" | p4 login -a   # writes ticket to ~/.p4tickets
# From here the agent can connect without re-supplying the password
```

### Agent Best Practices

A few things to keep in mind when writing agent workflows with this skill:

- **Give the agent a dedicated workspace name** (e.g. `agent-<hostname>-<task>`) so it never collides with a human developer's workspace.
- **Include a cleanup step** at the end of your workflow prompt (e.g. *"revert any unchanged files when done"*) to avoid leaving open files on the server.
- **Prefer numbered changelists** — ask the agent to create a named changelist rather than using the default one, so it's easy to identify and roll back if something goes wrong.
- **For merge tasks**, tell the agent which resolve strategy you want (`auto-merge`, `accept source`, `accept target`) so it doesn't have to guess.

---

## File Structure

```
perforce/
├── README.md               ← You are here
├── SKILL.md                ← Main skill instructions (loaded by agent)
└── references/
    └── branching.md        ← Deep reference: streams, integrate, resolve
```

---

## Contributing

PRs welcome. If you add support for additional workflows (shelving, jobs, labels, P4 triggers), follow the existing section structure in `SKILL.md` and add a corresponding entry to the **What This Skill Covers** table above.
