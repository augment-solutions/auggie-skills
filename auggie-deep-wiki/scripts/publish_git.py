#!/usr/bin/env python3
"""Publish a generated deep-wiki to a Git-backed Astro site repository.

Replaces the older publish_vercel.py (which shelled out to the Vercel
CLI from the local machine). The Git-backed flow works in any
environment that can invoke ``git`` and reach the host's git server —
including the ephemeral Poseidon sandbox where the auggie-deep-wiki
expert runs.

Pipeline:
  1. Resolve ``--wiki-repo`` (or ``DEEP_WIKIS_GIT_REPO`` env var).
     This is **required**; the skill ships no default host repo so
     teams can self-host the published site.
  2. ``git clone --depth=1 --branch <branch> <wiki_repo>`` into a temp
     dir. If ``GITHUB_TOKEN`` is set, inject it as an HTTP
     ``Authorization: Bearer ...`` header for the duration of the
     clone/push (no token written to ``.git/config`` or logs).
  3. Build the Astro content-collection entry from
     ``<output_dir>/wiki.mdx`` + ``repo_metadata.json``. Replace
     ``src/content/wikis/<slug>/`` atomically (rm + write) so stale
     auxiliary files from a previous run don't linger.
  4. ``git add`` / ``commit``. Skip the commit when the index is empty
     (idempotent re-run with identical content).
  5. ``git push origin <branch>`` with a small rebase-and-retry loop
     to survive concurrent pushes from other skill sessions.

Vercel: this script does not call the Vercel CLI. The host repo is
expected to be wired to a Vercel project (or any other static-site
host) that auto-deploys on push.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("auggie-deep-wiki.publish-git")

WIKI_REPO_ENV = "DEEP_WIKIS_GIT_REPO"
DEFAULT_BRANCH = "main"
CONTENT_SUBPATH = Path("src/content/wikis")
CLONE_TIMEOUT = 300
PUSH_TIMEOUT = 300
PUSH_RETRIES = 3


class PublishError(RuntimeError):
    """Raised when the publish pipeline cannot continue."""


@dataclass
class PublishResult:
    repo_url: str
    branch: str
    slug: str
    entry_path: Path
    commit_sha: str | None
    pushed: bool


# ---------------------------------------------------------------------------
# subprocess helper
# ---------------------------------------------------------------------------
def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    log.debug("$ %s (cwd=%s)", " ".join(cmd), cwd)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        out = (proc.stdout or "") + (proc.stderr or "")
        raise PublishError(
            f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\n{out}"
        )
    return proc


# ---------------------------------------------------------------------------
# slug + frontmatter helpers (unchanged behaviour from publish_vercel.py)
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def derive_slug(repo_url: str | None, metadata: dict[str, Any] | None) -> str:
    """Stable, lowercase slug for a repo. ``owner-name`` when possible."""
    if metadata:
        owner = str(metadata.get("owner") or "").strip().lower()
        name = str(metadata.get("name") or metadata.get("repo_name") or "").strip().lower()
        if owner and name:
            return _SLUG_RE.sub("-", f"{owner}-{name}").strip("-")
        if name:
            return _SLUG_RE.sub("-", name).strip("-")
    if repo_url:
        clean = repo_url.rstrip("/").removesuffix(".git")
        parts = clean.split("/")
        if len(parts) >= 2:
            return _SLUG_RE.sub("-", f"{parts[-2]}-{parts[-1]}".lower()).strip("-")
    return "wiki"


def _yaml_scalar(value: Any) -> str:
    """Conservative YAML scalar emitter for frontmatter values.

    Handles strings (always quoted, with control characters escaped),
    numbers, booleans, and lists of strings.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_yaml_scalar(v) for v in value)
        return f"[{items}]"
    s = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{s}"'


def _strip_existing_frontmatter(mdx: str) -> str:
    """Drop the bookend ``---`` lines (if any) from ``wiki.mdx``."""
    lines = mdx.splitlines()
    while lines and lines[0].strip() == "":
        lines.pop(0)
    if lines and lines[0].strip() == "---":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    if lines and lines[-1].strip() == "---":
        lines.pop()
    return "\n".join(lines).strip("\n")


def build_entry_mdx(
    *,
    wiki_mdx: str,
    metadata: dict[str, Any],
    structure: dict[str, Any] | None,
) -> str:
    """Wrap ``wiki.mdx`` body in a valid Astro content-collection entry."""
    body = _strip_existing_frontmatter(wiki_mdx)
    title = (
        (structure or {}).get("title")
        or metadata.get("name")
        or metadata.get("repo_name")
        or "Wiki"
    )
    description = (
        (structure or {}).get("description")
        or metadata.get("github_description")
        or metadata.get("description")
        or ""
    )

    fm: dict[str, Any] = {"title": title}
    if description:
        fm["description"] = description
    if metadata.get("repo_url"):
        fm["repo_url"] = metadata["repo_url"]
    if metadata.get("commit_date"):
        fm["last_updated"] = metadata["commit_date"]
    if metadata.get("commit_hash"):
        fm["commit_hash"] = metadata["commit_hash"]
    if metadata.get("commit_hash_short"):
        fm["commit_hash_short"] = metadata["commit_hash_short"]
    if isinstance(metadata.get("github_stars"), int):
        fm["stars"] = metadata["github_stars"]
    if metadata.get("github_language"):
        fm["language"] = metadata["github_language"]
    topics = metadata.get("github_topics")
    if isinstance(topics, list) and topics:
        fm["topics"] = [str(t) for t in topics]

    fm_lines = ["---"] + [f"{k}: {_yaml_scalar(v)}" for k, v in fm.items()] + ["---", ""]
    return "\n".join(fm_lines) + body.strip() + "\n"


# ---------------------------------------------------------------------------
# git operations
# ---------------------------------------------------------------------------
def check_git() -> None:
    if shutil.which("git") is None:
        raise PublishError("`git` is required but not found in PATH.")


def _git_base(token: str | None) -> list[str]:
    """Build a ``git`` invocation prefix, injecting an auth header when
    a token is provided so it never lands in ``.git/config`` or logs."""
    cmd = ["git"]
    if token:
        # Scoped to a single invocation via -c. ``Authorization: Bearer``
        # works for both GitHub-issued tokens (classic + fine-grained)
        # and most other Git providers that accept bearer auth.
        cmd += ["-c", f"http.extraHeader=Authorization: Bearer {token}"]
    return cmd


def _resolve_token() -> str | None:
    """Return a token from the standard env vars, if any.

    Order: ``GITHUB_TOKEN`` (Poseidon sandbox + GitHub Actions), then
    ``GH_TOKEN`` (gh CLI). Empty values are treated as unset.
    """
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return None


def clone_host_repo(
    repo_url: str,
    branch: str,
    work_dir: Path,
    *,
    token: str | None,
) -> None:
    """Shallow-clone the host repo into ``work_dir`` (must not exist)."""
    if work_dir.exists():
        raise PublishError(f"Clone target already exists: {work_dir}")
    cmd = _git_base(token) + [
        "clone",
        "--depth=1",
        "--branch",
        branch,
        repo_url,
        str(work_dir),
    ]
    log.info("Cloning %s (branch=%s) -> %s", repo_url, branch, work_dir)
    _run(cmd, capture=True, timeout=CLONE_TIMEOUT)


def write_entry(
    work_dir: Path,
    slug: str,
    *,
    wiki_mdx: str,
    metadata: dict[str, Any],
    structure: dict[str, Any] | None,
) -> Path:
    """Replace ``src/content/wikis/<slug>/`` with a fresh ``index.mdx``."""
    target_dir = work_dir / CONTENT_SUBPATH / slug
    if target_dir.exists():
        log.info("Replacing existing entry %s", target_dir.relative_to(work_dir))
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    entry = target_dir / "index.mdx"
    entry.write_text(
        build_entry_mdx(wiki_mdx=wiki_mdx, metadata=metadata, structure=structure),
        encoding="utf-8",
    )
    return entry


def _has_staged_changes(work_dir: Path) -> bool:
    """``True`` when ``git diff --cached`` reports a non-empty index."""
    proc = _run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=work_dir,
        check=False,
        capture=True,
    )
    return proc.returncode != 0


def commit_and_push(
    work_dir: Path,
    *,
    slug: str,
    branch: str,
    push: bool,
    token: str | None,
    author_name: str,
    author_email: str,
) -> tuple[str | None, bool]:
    """Stage, commit, and (optionally) push. Returns ``(sha, pushed)``.

    ``sha`` is ``None`` when there were no changes to commit (idempotent
    re-run with identical content). ``pushed`` is ``False`` when ``push``
    was disabled or there was nothing to push.
    """
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
    )
    _run(["git", "add", "-A"], cwd=work_dir, env=env, capture=True)
    if not _has_staged_changes(work_dir):
        log.info("No changes for slug %s; skipping commit", slug)
        return None, False
    msg = f"deep-wiki: update {slug}"
    _run(["git", "commit", "-m", msg], cwd=work_dir, env=env, capture=True)
    sha_proc = _run(
        ["git", "rev-parse", "HEAD"], cwd=work_dir, capture=True
    )
    sha = (sha_proc.stdout or "").strip() or None

    if not push:
        log.info("Skipping push (--no-push); commit %s left in %s", sha, work_dir)
        return sha, False

    last_err: PublishError | None = None
    for attempt in range(1, PUSH_RETRIES + 1):
        cmd = _git_base(token) + ["push", "origin", branch]
        proc = _run(cmd, cwd=work_dir, env=env, check=False, capture=True, timeout=PUSH_TIMEOUT)
        if proc.returncode == 0:
            log.info("Pushed %s to origin/%s", sha[:8] if sha else "?", branch)
            return sha, True
        err = (proc.stderr or "") + (proc.stdout or "")
        if "non-fast-forward" in err or "fetch first" in err or "rejected" in err:
            log.warning(
                "Push rejected (concurrent update?); rebasing and retrying [%d/%d]",
                attempt,
                PUSH_RETRIES,
            )
            pull_cmd = _git_base(token) + ["pull", "--rebase", "origin", branch]
            _run(pull_cmd, cwd=work_dir, env=env, capture=True, timeout=PUSH_TIMEOUT)
            last_err = PublishError(err.strip())
            continue
        raise PublishError(f"git push failed: {err.strip()}")
    raise PublishError(
        f"git push failed after {PUSH_RETRIES} retries: {last_err}"
    )


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s: %s", path, exc)
        return {}


def publish(
    *,
    output_dir: Path,
    wiki_repo: str | None = None,
    branch: str = DEFAULT_BRANCH,
    slug: str | None = None,
    push: bool = True,
    work_dir: Path | None = None,
    keep_work_dir: bool = False,
    author_name: str = "auggie-deep-wiki",
    author_email: str = "auggie-deep-wiki@users.noreply.github.com",
) -> PublishResult:
    """Publish ``<output_dir>/wiki.mdx`` to the host Astro repository."""
    check_git()

    repo_url = (wiki_repo or os.environ.get(WIKI_REPO_ENV) or "").strip()
    if not repo_url:
        raise PublishError(
            f"No host repo configured. Pass --wiki-repo or set "
            f"${WIKI_REPO_ENV} to the URL of the Astro site repo "
            f"(e.g. https://github.com/<org>/deep-wikis.git)."
        )

    output_dir = Path(output_dir).resolve()
    wiki_path = output_dir / "wiki.mdx"
    if not wiki_path.exists():
        raise PublishError(f"Missing input: {wiki_path}")
    wiki_mdx = wiki_path.read_text(encoding="utf-8")
    metadata = _load_optional_json(output_dir / "repo_metadata.json")
    structure = _load_optional_json(output_dir / "wiki_structure.json")

    final_slug = slug or derive_slug(metadata.get("repo_url"), metadata)
    log.info("Slug: %s", final_slug)

    token = _resolve_token()
    if token:
        log.info("Auth: HTTP Authorization header (from env)")
    else:
        log.info("Auth: relying on git's default credentials")

    cleanup_dir: Path | None = None
    try:
        if work_dir is None:
            tmp = Path(tempfile.mkdtemp(prefix="deep-wikis-clone-"))
            cleanup_dir = tmp if not keep_work_dir else None
            target = tmp / "deep-wikis"
        else:
            target = Path(work_dir).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)

        clone_host_repo(repo_url, branch, target, token=token)
        entry = write_entry(
            target,
            final_slug,
            wiki_mdx=wiki_mdx,
            metadata=metadata,
            structure=structure or None,
        )
        sha, pushed = commit_and_push(
            target,
            slug=final_slug,
            branch=branch,
            push=push,
            token=token,
            author_name=author_name,
            author_email=author_email,
        )
        return PublishResult(
            repo_url=repo_url,
            branch=branch,
            slug=final_slug,
            entry_path=entry.relative_to(target),
            commit_sha=sha,
            pushed=pushed,
        )
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="publish_git",
        description=(
            "Publish a generated deep-wiki to a Git-backed Astro site repo. "
            "Clones the repo, writes src/content/wikis/<slug>/index.mdx, "
            "commits, and pushes."
        ),
    )
    p.add_argument("--output-dir", required=True, help="Directory containing wiki.mdx")
    p.add_argument(
        "--wiki-repo",
        default=os.environ.get(WIKI_REPO_ENV),
        help=(
            f"URL of the host Astro repo (HTTPS or SSH). Required; can also "
            f"be set via ${WIKI_REPO_ENV}."
        ),
    )
    p.add_argument("--branch", default=DEFAULT_BRANCH)
    p.add_argument("--slug", default=None, help="Override the auto-derived slug")
    p.add_argument(
        "--no-push",
        action="store_true",
        help="Commit locally only; do not push (debugging/dry-run)",
    )
    p.add_argument(
        "--work-dir",
        default=None,
        help="Persistent clone path (default: ephemeral temp dir)",
    )
    p.add_argument(
        "--keep-work-dir",
        action="store_true",
        help="Keep the clone directory after a successful run (default temp dir is removed)",
    )
    p.add_argument(
        "--author-name", default="auggie-deep-wiki", help="git author/committer name"
    )
    p.add_argument(
        "--author-email",
        default="auggie-deep-wiki@users.noreply.github.com",
        help="git author/committer email",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        result = publish(
            output_dir=Path(args.output_dir),
            wiki_repo=args.wiki_repo,
            branch=args.branch,
            slug=args.slug,
            push=not args.no_push,
            work_dir=Path(args.work_dir) if args.work_dir else None,
            keep_work_dir=args.keep_work_dir,
            author_name=args.author_name,
            author_email=args.author_email,
        )
    except PublishError as exc:
        log.error("Publish failed: %s", exc)
        return 1
    log.info(
        "✓ slug=%s entry=%s commit=%s pushed=%s",
        result.slug,
        result.entry_path,
        result.commit_sha or "<no-change>",
        result.pushed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
