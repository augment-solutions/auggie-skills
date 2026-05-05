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
NPM_INSTALL_TIMEOUT = 600
BUILD_TIMEOUT = 600
# How many trailing lines of build output to surface in the PublishError so
# the user can see what went wrong without drowning in npm noise.
BUILD_OUTPUT_TAIL_LINES = 80


class PublishError(RuntimeError):
    """Raised when the publish pipeline cannot continue."""


class BuildToolingMissing(RuntimeError):
    """Raised when ``npm`` / ``node`` aren't on PATH so we cannot run
    ``astro build``.  ``publish()`` catches this to skip the build
    validation step (and the push) with an actionable summary instead
    of aborting with a hard error.
    """


@dataclass
class PublishResult:
    repo_url: str
    branch: str
    slug: str
    entry_path: Path
    commit_sha: str | None
    pushed: bool
    # Set when ``validate_astro_build`` could not run (e.g. ``npm`` missing
    # in the publish environment).  When ``True`` the entry was written
    # locally but the host repo was *not* committed/pushed; the operator
    # has to run ``npm install && npm run build`` manually before pushing.
    validation_skipped: bool = False
    validation_skipped_reason: str | None = None


# ---------------------------------------------------------------------------
# subprocess helper
# ---------------------------------------------------------------------------
_AUTH_HEADER_RE = re.compile(
    r"(http\.extraHeader=Authorization:\s*Bearer\s+)\S+", re.IGNORECASE
)
_URL_USERINFO_RE = re.compile(r"(https?://)[^/@\s]+@")


def _redact(text: str) -> str:
    """Strip credentials from a string before it lands in logs or errors."""
    text = _AUTH_HEADER_RE.sub(r"\1***", text)
    text = _URL_USERINFO_RE.sub(r"\1***@", text)
    return text


def _redact_cmd(cmd: list[str]) -> str:
    """Render ``cmd`` as a shell-ish string with auth tokens masked."""
    return _redact(" ".join(cmd))


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    safe_cmd = _redact_cmd(cmd)
    log.debug("$ %s (cwd=%s)", safe_cmd, cwd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError(
            f"Command timed out after {exc.timeout}s: {safe_cmd}"
        ) from exc
    except OSError as exc:
        raise PublishError(f"Command not runnable ({exc}): {safe_cmd}") from exc
    if check and proc.returncode != 0:
        out = _redact((proc.stdout or "") + (proc.stderr or ""))
        raise PublishError(
            f"Command failed (exit {proc.returncode}): {safe_cmd}\n{out}"
        )
    return proc


# ---------------------------------------------------------------------------
# slug + frontmatter helpers (unchanged behaviour from publish_vercel.py)
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Slugs become directory names under ``src/content/wikis/``; the regex below
# is the *only* shape we accept after sanitization.  It deliberately disallows
# path separators, ``..``, leading dots, and any non-ASCII character so a
# malicious or malformed value cannot escape ``CONTENT_SUBPATH``.
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


def _sanitize_slug(slug: str) -> str:
    """Return ``slug`` if it matches ``_SAFE_SLUG_RE``, else raise.

    Used at both the CLI boundary (user-supplied ``--slug``) and the
    filesystem boundary in :func:`write_entry` so any future caller
    inherits the same guard.
    """
    candidate = (slug or "").strip()
    if not _SAFE_SLUG_RE.match(candidate):
        raise PublishError(
            "Invalid slug: must match [a-z0-9][a-z0-9_-]{0,99} "
            f"(got {slug!r})"
        )
    return candidate


_SLUG_FALLBACK = "wiki"
# ``_SAFE_SLUG_RE`` permits up to 100 chars total (1 leading + 99 trailing).
_SLUG_MAX_LEN = 100


def _coerce_safe_slug(raw: str) -> str:
    """Project ``raw`` onto :data:`_SAFE_SLUG_RE`.

    Always returns a value that passes :func:`_sanitize_slug` so the
    publish flow keeps working even when metadata is junk (empty,
    all-symbols, very long ``owner/name``).  Used by :func:`derive_slug`
    so callers never have to think about edge cases.
    """
    candidate = _SLUG_RE.sub("-", (raw or "").lower()).strip("-_")
    if len(candidate) > _SLUG_MAX_LEN:
        candidate = candidate[:_SLUG_MAX_LEN].rstrip("-_")
    if not candidate:
        return _SLUG_FALLBACK
    # Post-strip the first char is guaranteed alphanumeric (``-`` and ``_``
    # were stripped above and ``_SLUG_RE`` collapsed everything else), so
    # ``candidate`` matches ``_SAFE_SLUG_RE`` by construction.
    return candidate


def derive_slug(repo_url: str | None, metadata: dict[str, Any] | None) -> str:
    """Stable, lowercase slug for a repo. ``owner-name`` when possible.

    The result is guaranteed to match :data:`_SAFE_SLUG_RE` (and therefore
    to pass :func:`_sanitize_slug`) so ``--publish-git`` never hard-fails
    on quirky upstream metadata; junk inputs collapse to
    ``"wiki"`` and overlong inputs are truncated to
    :data:`_SLUG_MAX_LEN` characters.
    """
    raw = ""
    if metadata:
        owner = str(metadata.get("owner") or "").strip().lower()
        name = str(metadata.get("name") or metadata.get("repo_name") or "").strip().lower()
        if owner and name:
            raw = f"{owner}-{name}"
        elif name:
            raw = name
    if not raw and repo_url:
        clean = repo_url.rstrip("/").removesuffix(".git")
        parts = clean.split("/")
        if len(parts) >= 2:
            raw = f"{parts[-2]}-{parts[-1]}".lower()
    return _coerce_safe_slug(raw)


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


def _refresh_existing_clone(
    repo_url: str,
    branch: str,
    work_dir: Path,
    *,
    token: str | None,
) -> None:
    """Bring a reusable persistent clone up to date with ``origin/<branch>``.

    Resets the local branch to the remote tip so a stale checkout from
    a previous run cannot leak old state into the new commit.
    """
    git_dir = work_dir / ".git"
    if not git_dir.is_dir():
        raise PublishError(
            f"Clone target exists but is not a git repository: {work_dir}"
        )
    log.info("Reusing existing clone at %s; refreshing %s", work_dir, branch)
    base = _git_base(token)
    _run(
        base + ["remote", "set-url", "origin", repo_url],
        cwd=work_dir, capture=True, timeout=CLONE_TIMEOUT,
    )
    _run(
        base + ["fetch", "--depth=1", "origin", branch],
        cwd=work_dir, capture=True, timeout=CLONE_TIMEOUT,
    )
    _run(
        base + ["checkout", "-B", branch, f"origin/{branch}"],
        cwd=work_dir, capture=True, timeout=CLONE_TIMEOUT,
    )
    _run(
        base + ["reset", "--hard", f"origin/{branch}"],
        cwd=work_dir, capture=True, timeout=CLONE_TIMEOUT,
    )
    # ``-fd`` (without ``x``) deliberately preserves gitignored
    # paths.  In practice that means ``node_modules/`` and ``dist/``
    # survive across re-runs of ``--wiki-work-dir``, so the
    # ``validate_astro_build`` step can skip ``npm install`` and reuse
    # cached deps.  Tracked files are still reset to ``origin/<branch>``
    # by the preceding ``reset --hard`` so the working tree stays
    # pristine.
    _run(
        base + ["clean", "-fd"],
        cwd=work_dir, capture=True, timeout=CLONE_TIMEOUT,
    )


def clone_host_repo(
    repo_url: str,
    branch: str,
    work_dir: Path,
    *,
    token: str | None,
) -> None:
    """Provision ``work_dir`` to point at ``origin/<branch>``.

    If ``work_dir`` is missing, do a shallow clone.  If it already
    contains a git checkout, refresh it in place so ``--work-dir`` and
    ``--keep-work-dir`` can be reused across runs without manual cleanup.
    A non-empty, non-git directory is rejected to avoid clobbering
    unrelated content.
    """
    if work_dir.exists():
        # ``Path.is_dir()`` follows symlinks, which is fine here: a symlink
        # pointing at a directory is treated like the directory it targets.
        # We explicitly reject regular files so ``iterdir()`` (which would
        # raise ``NotADirectoryError``) is never reached.
        if not work_dir.is_dir():
            raise PublishError(
                f"Clone target exists and is not a directory: {work_dir}"
            )
        if (work_dir / ".git").is_dir():
            _refresh_existing_clone(repo_url, branch, work_dir, token=token)
            return
        if any(work_dir.iterdir()):
            raise PublishError(
                "Clone target exists and is not a git repository "
                f"(refusing to overwrite): {work_dir}"
            )
        # Empty directory — git clone refuses to clone into it, so remove first.
        work_dir.rmdir()
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
    """Replace ``src/content/wikis/<slug>/`` with a fresh ``index.mdx``.

    The ``slug`` is sanitized once more here (defense in depth) so the
    resulting ``target_dir`` cannot escape ``CONTENT_SUBPATH``.  We also
    verify that ``base_dir`` and ``target_dir`` resolve to a path under
    the canonical ``work_dir``: a malicious host repo could symlink
    ``src/content/wikis`` (or any of its parents) outside the clone, and
    without this check the subsequent ``rmtree``/``write_text`` would
    happily operate on the symlink target.
    """
    safe_slug = _sanitize_slug(slug)
    work_root = work_dir.resolve()
    base_dir = (work_dir / CONTENT_SUBPATH).resolve()
    target_dir = (base_dir / safe_slug).resolve()
    for candidate in (base_dir, target_dir):
        try:
            candidate.relative_to(work_root)
        except ValueError as exc:
            raise PublishError(
                f"Refusing to write outside clone root: {candidate} "
                f"(work_dir={work_root})"
            ) from exc
    # Belt-and-braces: also reject a target that escaped ``base_dir``
    # via slug normalization (already prevented by ``_sanitize_slug``,
    # but cheap to keep here).
    try:
        target_dir.relative_to(base_dir)
    except ValueError as exc:
        raise PublishError(
            f"Refusing to write outside content collection: {target_dir}"
        ) from exc
    if target_dir.exists():
        log.info("Replacing existing entry %s", target_dir.relative_to(work_root))
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    entry = target_dir / "index.mdx"
    entry.write_text(
        build_entry_mdx(wiki_mdx=wiki_mdx, metadata=metadata, structure=structure),
        encoding="utf-8",
    )
    return entry


# ---------------------------------------------------------------------------
# astro build validation
# ---------------------------------------------------------------------------
def _check_build_toolchain() -> str | None:
    """Return ``None`` when ``npm`` and ``node`` are both on PATH.

    Otherwise return a human-readable reason that the caller surfaces in
    a skip-with-summary message.  We deliberately do not check versions:
    the host repo's ``package.json`` is what dictates compatibility, and
    asking the operator to upgrade is more useful than asking us to.
    """
    missing: list[str] = []
    for tool in ("node", "npm"):
        if shutil.which(tool) is None:
            missing.append(tool)
    if missing:
        return (
            f"required build tooling not found on PATH: {', '.join(missing)} "
            "(install Node.js, then re-run, or pass --skip-build-validation "
            "to bypass and push without local validation)"
        )
    return None


def _tail_output(text: str, *, limit: int = BUILD_OUTPUT_TAIL_LINES) -> str:
    """Return at most ``limit`` trailing lines of ``text`` for error reports."""
    lines = text.splitlines()
    if len(lines) <= limit:
        return text.rstrip()
    return "...\n" + "\n".join(lines[-limit:]).rstrip()


def _npm_install(work_dir: Path, *, timeout: int = NPM_INSTALL_TIMEOUT) -> None:
    """Populate ``node_modules`` if it is missing.

    No-op when ``node_modules`` already exists so persistent ``--work-dir``
    runs don't pay the install cost on every publish.  We avoid ``npm ci``
    because the host repo template ships without a lock file; ``npm
    install`` is the lowest-friction option.
    """
    if (work_dir / "node_modules").is_dir():
        log.info("node_modules already present in %s; skipping npm install", work_dir)
        return
    log.info("Installing host-repo dependencies (npm install) in %s", work_dir)
    proc = _run(
        ["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"],
        cwd=work_dir,
        capture=True,
        check=False,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise PublishError(
            "npm install failed in the host repo clone "
            f"({work_dir}); cannot validate astro build:\n"
            f"{_tail_output(_redact((proc.stderr or '') + (proc.stdout or '')))}"
        )


def validate_astro_build(
    work_dir: Path,
    *,
    install_timeout: int = NPM_INSTALL_TIMEOUT,
    build_timeout: int = BUILD_TIMEOUT,
) -> None:
    """Run ``astro build`` in ``work_dir`` to catch invalid MDX/YAML.

    The Astro static-site host repo is auto-deployed on push, so a broken
    ``index.mdx`` (e.g. malformed YAML frontmatter, unclosed Mermaid
    block) only surfaces as a Vercel/Netlify deploy failure long after
    the bad commit has already landed.  Running the build locally before
    pushing keeps the host repo green.

    Raises:
        BuildToolingMissing: when ``npm``/``node`` aren't on PATH; the
            caller is expected to skip the publish with a summary.
        PublishError: when the build itself fails (including ``npm
            install`` errors).  Push must be skipped in that case.
    """
    reason = _check_build_toolchain()
    if reason is not None:
        raise BuildToolingMissing(reason)
    if not (work_dir / "package.json").is_file():
        # The host repo *should* always have a package.json next to the
        # ``src/content/wikis/`` collection; if it doesn't, the project
        # isn't an Astro site and we have nothing to validate.
        raise PublishError(
            f"Host repo at {work_dir} has no package.json; "
            "cannot run astro build to validate the new entry."
        )
    _npm_install(work_dir, timeout=install_timeout)
    log.info("Validating with `npm run build` (astro build) in %s", work_dir)
    proc = _run(
        ["npm", "run", "build", "--silent"],
        cwd=work_dir,
        capture=True,
        check=False,
        timeout=build_timeout,
    )
    if proc.returncode != 0:
        combined = _redact((proc.stderr or "") + (proc.stdout or ""))
        raise PublishError(
            "astro build failed against the new entry; refusing to push. "
            "Inspect the entry, fix the source, and re-run.\n"
            f"--- last {BUILD_OUTPUT_TAIL_LINES} lines of build output ---\n"
            f"{_tail_output(combined)}"
        )
    log.info("astro build OK")


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

    def _head_sha() -> str | None:
        proc = _run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture=True)
        return (proc.stdout or "").strip() or None

    sha = _head_sha()

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
        err = _redact((proc.stderr or "") + (proc.stdout or ""))
        # Only retry on the canonical non-fast-forward signals git emits when
        # the remote tip moved between fetch and push.  A bare ``"rejected"``
        # match would also fire on protected-branch / pre-receive hook
        # rejections, which rebase-and-retry cannot fix - those should
        # surface to the user immediately.
        if (
            "non-fast-forward" in err
            or "fetch first" in err
            or "tip of your current branch is behind" in err
        ):
            log.warning(
                "Push rejected (concurrent update?); rebasing and retrying [%d/%d]",
                attempt,
                PUSH_RETRIES,
            )
            pull_cmd = _git_base(token) + ["pull", "--rebase", "origin", branch]
            _run(pull_cmd, cwd=work_dir, env=env, capture=True, timeout=PUSH_TIMEOUT)
            # Rebase rewrites the local commit, so refresh ``sha`` before
            # retrying so the success-path log and the returned value
            # reflect the actual commit being pushed.
            sha = _head_sha()
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
    skip_build_validation: bool = False,
) -> PublishResult:
    """Publish ``<output_dir>/wiki.mdx`` to the host Astro repository.

    Before pushing, runs ``astro build`` against the cloned host repo so
    that malformed MDX/YAML never lands on the deployed site.  When
    ``npm``/``node`` aren't available the validation step is skipped and
    the publish is aborted with a summary so the operator can re-run
    elsewhere; pass ``skip_build_validation=True`` to bypass entirely
    (e.g. CI environments that explicitly trust the input).
    """
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

    final_slug = _sanitize_slug(
        slug or derive_slug(metadata.get("repo_url"), metadata)
    )
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
            # When --no-push is used we deliberately keep the temp dir so the
            # operator can inspect the dry-run commit; otherwise honour
            # ``keep_work_dir``.  An explicit ``--work-dir`` is never owned
            # by us and is left alone.
            if push and not keep_work_dir:
                cleanup_dir = tmp
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

        # Validate before any commit/push so a broken MDX/YAML entry
        # never reaches the host repo.  Three outcomes:
        #   1. validation passes -> commit + push as usual.
        #   2. tooling missing   -> skip both validation and push, keep
        #      the work dir, and surface a manual-recovery summary.
        #   3. build fails       -> propagate PublishError; no push.
        validation_skipped = False
        validation_skipped_reason: str | None = None
        if skip_build_validation:
            validation_skipped = True
            validation_skipped_reason = "explicitly bypassed (--skip-build-validation)"
            log.warning(
                "Build validation bypassed; pushing without local astro build"
            )
        else:
            try:
                validate_astro_build(target)
            except BuildToolingMissing as exc:
                validation_skipped = True
                validation_skipped_reason = str(exc)
                log.warning(
                    "Build validation skipped: %s. Refusing to push so the "
                    "host repo stays green; entry left at %s for manual "
                    "review.",
                    exc,
                    entry,
                )
                # Do not push; preserve the work dir for manual inspection.
                cleanup_dir = None
                return PublishResult(
                    repo_url=repo_url,
                    branch=branch,
                    slug=final_slug,
                    entry_path=entry.resolve().relative_to(target.resolve()),
                    commit_sha=None,
                    pushed=False,
                    validation_skipped=validation_skipped,
                    validation_skipped_reason=validation_skipped_reason,
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
        if not push:
            log.info("Work dir preserved at %s (use --no-push only for dry runs)", target)
        return PublishResult(
            repo_url=repo_url,
            branch=branch,
            slug=final_slug,
            # Both sides resolved so the relative_to works regardless of
            # symlinks introduced by ``tempfile.mkdtemp`` on macOS
            # (``/var/folders`` -> ``/private/var/folders``).
            entry_path=entry.resolve().relative_to(target.resolve()),
            commit_sha=sha,
            pushed=pushed,
            validation_skipped=validation_skipped,
            validation_skipped_reason=validation_skipped_reason,
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
    p.add_argument(
        "--skip-build-validation",
        action="store_true",
        help=(
            "Skip the local `astro build` step that catches malformed "
            "MDX/YAML before pushing. Use only when the host repo has "
            "out-of-band validation in CI."
        ),
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
            skip_build_validation=args.skip_build_validation,
        )
    except PublishError as exc:
        log.error("Publish failed: %s", exc)
        return 1
    if result.validation_skipped and not result.pushed:
        log.error(
            "Build validation skipped (%s) and push aborted. The new "
            "entry is at %s; install Node.js, then run "
            "`cd <work-dir> && npm install && npm run build && git push` "
            "to publish manually, or pass --skip-build-validation to "
            "bypass on the next run.",
            result.validation_skipped_reason,
            result.entry_path,
        )
        return 3
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
