# Perforce Branching & Merging — Deep Reference

## Streams vs. Classic Branches

| Feature | Streams | Classic Branches |
|---|---|---|
| Setup | `p4 stream` spec | `p4 branch` spec or manual depot paths |
| Merge command | `p4 merge` / `p4 copy` | `p4 integrate` |
| Flow enforcement | Automatic (parent→child) | Manual |
| Best for | New projects, structured release trains | Legacy repos, custom topologies |

---

## Streams Workflow

### Stream Types (in order of stability)
```
mainline  ← most stable, the trunk
  ├── release/1.0   (release stream — only merges from main, no new dev)
  └── dev           (development stream — merges down from main, copies up)
        └── task/feature-x  (task stream — short-lived feature work)
```

### Merge Down (keep child in sync with parent)
```bash
# Switch your workspace to the child stream
p4 switch //depot/streams/dev

# Preview what would merge
p4 merge -n --from //depot/streams/main

# Perform the merge
p4 merge --from //depot/streams/main

# Resolve all scheduled files
p4 resolve -am          # auto-merge text conflicts
p4 resolve -as          # accept source for remaining
p4 resolve              # interactive resolve for unresolved

# Check status
p4 resolved             # what has been resolved
p4 opened               # files still pending

# Submit
p4 submit -d "Merge mainline into dev [$(date +%Y-%m-%d)]"
```

### Copy Up (promote changes to parent)
```bash
# Switch workspace to the parent stream
p4 switch //depot/streams/main

# Preview
p4 copy -n --from //depot/streams/dev

# Copy (no conflicts expected; copy-up is a clean propagation)
p4 copy --from //depot/streams/dev

# Resolve if needed (usually -as is appropriate)
p4 resolve -as

# Submit
p4 submit -d "Promote dev to mainline: feature-X"
```

### Creating a New Stream
```bash
# Create a development child stream under mainline
p4 stream -t development -P //depot/streams/main //depot/streams/dev

# Create a task stream (short-lived)
p4 stream -t task -P //depot/streams/dev //depot/streams/task/fix-123

# List streams
p4 streams //depot/streams/...
```

### P4Python: Streams
```python
# Fetch/create a stream spec
stream = p4.fetch_stream("//depot/streams/dev")
stream["Type"]   = "development"
stream["Parent"] = "//depot/streams/main"
p4.save_stream(stream)

# Merge down
p4.run_merge("--from", "//depot/streams/main")
p4.run_resolve("-am")
change = p4.fetch_change()
change["Description"] = "Merge main into dev"
p4.run_submit(change)
```

---

## Classic Branch Workflow (no Streams)

### Create a Branch Spec
```bash
p4 branch -o my-branch | sed \
  's|<enter description>|Release branch|; s|//depot/main/\.\.\.|//depot/main/... //depot/release/1.0/...|' \
  | p4 branch -i
```

Or use a here-doc:
```bash
p4 branch -i << 'EOF'
Branch: release-1.0
Owner:  agent_user
Description: Release 1.0 branch from main
View:
    //depot/main/... //depot/release/1.0/...
EOF
```

### Populate a New Branch (first time)
```bash
# Use p4 populate to seed without needing the files in your workspace
p4 populate -b release-1.0 -d "Initial branch of release 1.0"
```

### Integrate (merge between classic branches)
```bash
# Merge all unintegrated changes from main to release
p4 integrate -b release-1.0

# Or specify paths directly
p4 integrate //depot/main/... //depot/release/1.0/...

# Integrate a specific changelist only (cherry-pick)
p4 integrate -c 98765 //depot/main/... //depot/release/1.0/...

# Resolve and submit
p4 resolve -am
p4 submit -d "Integrate CL 98765 into release/1.0"
```

### P4Python: Classic Integrate
```python
# Integrate using branch spec
p4.run_integrate("-b", "release-1.0")
p4.run_resolve("-am")
change = p4.fetch_change()
change["Description"] = "Integrate main → release/1.0"
p4.run_submit(change)

# Integrate by path
p4.run_integrate("//depot/main/...", "//depot/release/1.0/...")
```

---

## Resolve Strategies

| Flag | Meaning | When to use |
|---|---|---|
| `-am` | Auto-merge | Text files with non-overlapping edits |
| `-as` | Accept source (theirs) | You want the incoming version |
| `-at` | Accept target (yours) | You want to keep your version |
| `-ay` | Accept yours | Same as `-at` |
| `-af` | Force accept (merge result may have markers) | Last resort; requires manual cleanup |
| _(none)_ | Interactive | Conflicting edits; needs human/tool decision |

### Auto-resolve all, then interactive for conflicts
```bash
p4 resolve -am          # auto-merge what we can
p4 resolve              # handle remaining conflicts interactively
```

### Check for unresolved files before submit
```bash
p4 resolve -n           # preview: what still needs resolving?
```

### P4Python resolve with callback
```python
class MyResolver(P4.Resolver):
    def resolve(self, mergeInfo):
        # mergeInfo.your_name, mergeInfo.their_name, etc.
        return "am"   # auto-merge; return "ay"/"as"/"at" to force

p4.run_resolve(resolver=MyResolver())
```

---

## Merge History & Auditing

```bash
# Show integration history for a file
p4 integrated //depot/main/src/file.c

# Show what changes have NOT yet been integrated
p4 interchanges //depot/main/... //depot/release/1.0/...

# Show full file history including integrations
p4 filelog -i //depot/release/1.0/src/file.c
```

```python
p4.run_integrated("//depot/main/src/file.c")
p4.run_interchanges("//depot/main/...", "//depot/release/1.0/...")
p4.run_filelog("-i", "//depot/release/1.0/src/file.c")
```

---

## Common Branching Pitfalls

1. **Merging when out of date:** Always `p4 sync` the target stream/branch before merging.
2. **Submitting unresolved files:** `p4 submit` will fail. Always run `p4 resolve -n` first
   to check.
3. **Skipping merge-down before copy-up:** Perforce enforces the flow; copy-up will fail if
   the child is not up to date with the parent in a Streams setup.
4. **Using default changelist for merge submits:** Always create a named changelist for
   traceability.
5. **Cherry-picking without `-Ob`:** When cherry-picking, use `p4 integrate -c CL# -Ob`
   to record the base revision and avoid re-integrating the same change later.
