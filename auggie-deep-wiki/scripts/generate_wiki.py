#!/usr/bin/env python3
"""Standalone deep-wiki generator that drives `auggie` headlessly.

Ports the orchestration in tools/deep-wiki/wiki_generation_task.py and the
runner pattern in tools/deep-wiki/augment_wiki_runner.py into a single,
stdlib-only script suitable for shipping inside an Augment skill.

Pipeline (all steps share one indexed workspace + one cache dir):
  1. git clone --depth 1 the target repo into a temp dir
  2. fetch GitHub metadata (stars, language, topics) via the public API
  3. run `auggie` with prompts/repo_metadata.txt -> repo_metadata.json
  4. run `auggie` with prompts/wiki_structure.txt -> wiki_structure.json
  5. for each section in the structure, run `auggie` with
     prompts/wiki_section.txt -> sections/<id>.mdx
  6. assemble sections/*.mdx into a single wiki.mdx (with last-updated line)
  7. clean up the workspace and cache temp dirs

Auth: relies on the user's existing Augment auth.
  - ~/.augment/.auggie.json (preferred) -> passed via --augment-session-json
  - or AUGMENT_API_TOKEN env var (auggie reads it natively)
  - run `auggie login` if neither is present.
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
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_API_URL = ""  # empty = let auggie use the tenantURL from the session file
DEFAULT_MODEL = "haiku4.5"
DEFAULT_TIMEOUT = 3600
CLONE_TIMEOUT = 300
RETRYABLE_TOKENS = ("502", "503", "504", "bad gateway", "unavailable")

log = logging.getLogger("auggie-deep-wiki")


# ---------------------------------------------------------------------------
# subprocess helpers
# ---------------------------------------------------------------------------
def _run(
    cmd: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing combined stdout+stderr as text."""
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\n"
            f"Output: {proc.stdout}"
        )
    return proc


def clone_repo(repo_url: str, target_dir: str) -> None:
    log.info("Cloning %s -> %s", repo_url, target_dir)
    _run(
        [
            "git", "clone", "--depth", "1", "--single-branch", "-q",
            repo_url, target_dir,
        ],
        timeout=CLONE_TIMEOUT,
    )


def get_commit_info(workspace_dir: str) -> dict[str, str] | None:
    try:
        h = _run(
            ["git", "rev-parse", "HEAD"], cwd=workspace_dir, timeout=30
        ).stdout.strip()
        d = _run(
            ["git", "show", "-s", "--format=%ci", "HEAD"],
            cwd=workspace_dir,
            timeout=30,
        ).stdout.strip()
        return {
            "commit_hash": h,
            "commit_hash_short": h[:7],
            "commit_date": d,
        }
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Failed to read commit info: %s", exc)
        return None


# ---------------------------------------------------------------------------
# GitHub metadata (stdlib http)
# ---------------------------------------------------------------------------
def fetch_github_metadata(repo_url: str) -> dict[str, Any] | None:
    parts = repo_url.rstrip("/").split("/")
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1].replace(".git", "")
    api = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        req = urllib.request.Request(api, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "name": data.get("name", repo),
            "owner": data.get("owner", {}).get("login", owner),
            "description": data.get("description"),
            "stars": data.get("stargazers_count"),
            "created_at": data.get("created_at"),
            "language": data.get("language"),
            "topics": data.get("topics", []),
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        log.warning("GitHub API fetch failed for %s: %s", repo_url, exc)
        return None


def escape_mdx_text(text: str) -> str:
    return (
        text.replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def slugify_id(title: str, used_ids: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "section"
    candidate, counter = base, 2
    while candidate in used_ids:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate


def load_prompt(prompts_dir: Path, name: str) -> str:
    path = prompts_dir / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text()


# ---------------------------------------------------------------------------
# auggie runner (single section)
# ---------------------------------------------------------------------------
def run_auggie_section(
    *,
    section_name: str,
    instruction_text: str,
    workspace_dir: str,
    cache_dir: str,
    output_file: Path,
    model: str,
    timeout: int,
    api_url: str,
    auggie_bin: str,
    max_retries: int = 3,
) -> str:
    """Run a single auggie invocation.

    auggie writes its result to ``output_file`` (referenced from the prompt
    via ``{output_file}``). We read it back after the process exits.
    """
    if output_file.exists():
        output_file.unlink()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_instruction.txt", prefix="deep_wiki_", delete=False
    ) as f:
        f.write(instruction_text)
        instruction_path = f.name

    cmd = [
        auggie_bin,
        "--workspace-root", workspace_dir,
        "--instruction-file", instruction_path,
        "--augment-cache-dir", cache_dir,
        "--model", model,
        "--print",
        "--allow-indexing",
    ]
    # Prefer ~/.augment/session.json (the OAuth session with accessToken +
    # tenantURL) over ~/.augment/.auggie.json (which is just CLI state).
    session_file = Path.home() / ".augment" / "session.json"
    legacy_session = Path.home() / ".augment" / ".auggie.json"
    if session_file.exists():
        cmd += ["--augment-session-json", str(session_file)]
    elif legacy_session.exists():
        cmd += ["--augment-session-json", str(legacy_session)]

    # Only override AUGMENT_API_URL when the caller explicitly supplied one;
    # otherwise let auggie pick up the tenantURL from the session file.
    env = {**os.environ}
    if api_url:
        env["AUGMENT_API_URL"] = api_url

    log.info("→ auggie [%s] (model=%s)", section_name, model)
    delay = 30
    last_err: Exception | None = None
    try:
        for attempt in range(1, max_retries + 1):
            t0 = time.monotonic()
            try:
                _run(cmd, cwd=workspace_dir, env=env, timeout=timeout)
                log.info(
                    "✓ auggie [%s] done in %.1fs", section_name, time.monotonic() - t0
                )
                break
            except RuntimeError as exc:
                last_err = exc
                msg = str(exc).lower()
                if attempt < max_retries and any(t in msg for t in RETRYABLE_TOKENS):
                    log.warning(
                        "Retryable auggie error (%d/%d): %s",
                        attempt,
                        max_retries,
                        exc,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 300)
                    continue
                raise
        else:  # pragma: no cover
            raise RuntimeError(f"auggie failed after {max_retries} attempts: {last_err}")
    finally:
        try:
            os.unlink(instruction_path)
        except OSError:
            pass

    if not output_file.exists():
        raise RuntimeError(
            f"auggie did not create expected output file: {output_file}"
        )
    return output_file.read_text().strip()



# ---------------------------------------------------------------------------
# wiki orchestration
# ---------------------------------------------------------------------------
def _aux_path(workspace_dir: str, name: str, ext: str) -> Path:
    """Auggie writes per-section outputs to workspace_dir/__deepwiki_<name>.<ext>."""
    return Path(workspace_dir) / f"__deepwiki_{name}.{ext}"


def generate_metadata(
    *,
    workspace_dir: str,
    cache_dir: str,
    prompts_dir: Path,
    output_dir: Path,
    repo_url: str,
    model: str,
    timeout: int,
    api_url: str,
    auggie_bin: str,
) -> dict[str, Any]:
    out_file = _aux_path(workspace_dir, "repo_metadata", "json")
    instruction = load_prompt(prompts_dir, "repo_metadata").format(
        output_file=str(out_file)
    )
    run_auggie_section(
        section_name="repo_metadata",
        instruction_text=instruction,
        workspace_dir=workspace_dir,
        cache_dir=cache_dir,
        output_file=out_file,
        model=model,
        timeout=timeout,
        api_url=api_url,
        auggie_bin=auggie_bin,
    )
    metadata = json.loads(out_file.read_text())

    gh = fetch_github_metadata(repo_url)
    if gh is None:
        log.warning("GitHub API unavailable; continuing without star/topic data")
    else:
        metadata["github_stars"] = gh.get("stars")
        metadata["github_description"] = gh.get("description")
        metadata["github_language"] = gh.get("language")
        metadata["github_topics"] = gh.get("topics", [])
        metadata.setdefault("created_at", gh.get("created_at"))

    commit = get_commit_info(workspace_dir)
    if commit:
        metadata.update(commit)
    metadata["repo_url"] = repo_url

    (output_dir / "repo_metadata.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def generate_structure(
    *,
    workspace_dir: str,
    cache_dir: str,
    prompts_dir: Path,
    output_dir: Path,
    model: str,
    timeout: int,
    api_url: str,
    auggie_bin: str,
) -> dict[str, Any]:
    out_file = _aux_path(workspace_dir, "wiki_structure", "json")
    instruction = load_prompt(prompts_dir, "wiki_structure").format(
        output_file=str(out_file)
    )
    run_auggie_section(
        section_name="wiki_structure",
        instruction_text=instruction,
        workspace_dir=workspace_dir,
        cache_dir=cache_dir,
        output_file=out_file,
        model=model,
        timeout=timeout,
        api_url=api_url,
        auggie_bin=auggie_bin,
    )
    raw = json.loads(out_file.read_text())
    title = raw.get("title") or "Wiki"
    description = raw.get("description") or ""
    raw_sections = raw.get("sections") or []
    if not isinstance(raw_sections, list) or not raw_sections:
        raise RuntimeError("wiki_structure.json must contain a non-empty 'sections' list")

    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for s in raw_sections:
        if not isinstance(s, dict):
            continue
        st = str(s.get("title") or "").strip()
        if not st:
            continue
        importance = str(s.get("importance") or "medium").lower()
        if importance not in {"high", "medium", "low"}:
            importance = "medium"
        file_paths = s.get("file_paths") or []
        if not isinstance(file_paths, list):
            file_paths = []
        file_paths = [str(p) for p in file_paths]
        raw_id = str(s.get("id") or "").strip().lower()
        if not raw_id or not all(c.isalnum() or c == "_" for c in raw_id):
            sid = slugify_id(st, used_ids)
        else:
            sid = raw_id if raw_id not in used_ids else slugify_id(st, used_ids)
        used_ids.add(sid)
        normalized.append(
            {"id": sid, "title": st, "importance": importance, "file_paths": file_paths}
        )

    titles_lower = [s["title"].lower() for s in normalized]
    if not any(t.startswith("overview") for t in titles_lower):
        oid = slugify_id("Overview", used_ids)
        used_ids.add(oid)
        normalized.insert(
            0, {"id": oid, "title": "Overview", "importance": "high", "file_paths": []}
        )
    if not any("architecture" in t for t in titles_lower):
        aid = slugify_id("Architecture", used_ids)
        used_ids.add(aid)
        normalized.insert(
            1 if normalized else 0,
            {"id": aid, "title": "Architecture", "importance": "high", "file_paths": []},
        )

    structure = {"title": title, "description": description, "sections": normalized}
    (output_dir / "wiki_structure.json").write_text(json.dumps(structure, indent=2))
    return structure


def generate_sections(
    *,
    workspace_dir: str,
    cache_dir: str,
    prompts_dir: Path,
    output_dir: Path,
    sections: list[dict[str, Any]],
    model: str,
    timeout: int,
    api_url: str,
    auggie_bin: str,
) -> dict[str, str]:
    sections_dir = output_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    template = load_prompt(prompts_dir, "wiki_section")
    contents: dict[str, str] = {}
    for idx, section in enumerate(sections, 1):
        sid = section["id"]
        log.info("Section %d/%d: %s (%s)", idx, len(sections), section["title"], sid)
        out_file = _aux_path(workspace_dir, sid, "mdx")
        file_paths = section.get("file_paths") or []
        file_list = (
            "\n".join(f"- {p}" for p in file_paths)
            if file_paths
            else "(No specific files were listed; use the most relevant parts of the repository as context.)"
        )
        instruction = template.format(
            output_file=str(out_file),
            section_title=section["title"],
            file_list=file_list,
        )
        content = run_auggie_section(
            section_name=sid,
            instruction_text=instruction,
            workspace_dir=workspace_dir,
            cache_dir=cache_dir,
            output_file=out_file,
            model=model,
            timeout=timeout,
            api_url=api_url,
            auggie_bin=auggie_bin,
        )
        (sections_dir / f"{sid}.mdx").write_text(content)
        contents[sid] = content
    return contents



def assemble_wiki(
    *,
    output_dir: Path,
    repo_url: str,
    structure: dict[str, Any],
    section_contents: dict[str, str],
    metadata: dict[str, Any],
) -> Path:
    """Assemble the per-section MDX files into a single ``wiki.mdx``.

    Mirrors the layout in tools/deep-wiki/wiki_generation_task.py:
      ---            (frontmatter open)
      # <title>
      Last updated on <date> (Commit: <hash-link>)
      ## <section title>
      <section content>
      ...
      ---            (frontmatter close)
    """
    title = structure.get("title") or "Wiki"
    sections = structure.get("sections") or []

    lines: list[str] = ["---", "", f"# {escape_mdx_text(title)}", ""]

    commit_date = metadata.get("commit_date")
    commit_short = metadata.get("commit_hash_short")
    commit_full = metadata.get("commit_hash")
    if commit_date and commit_short:
        try:
            dt = datetime.fromisoformat(
                commit_date.replace(" ", "T", 1).rsplit(" ", 1)[0]
            )
            formatted = dt.strftime("%b %d, %Y")
        except (ValueError, AttributeError):
            formatted = commit_date
        link = commit_short
        if "github.com" in repo_url and commit_full:
            clean = repo_url.rstrip("/")
            if clean.endswith(".git"):
                clean = clean[:-4]
            parts = clean.split("/")
            if len(parts) >= 2:
                link = f"[{commit_short}](https://github.com/{parts[-2]}/{parts[-1]}/commit/{commit_full})"
        lines.append(f"Last updated on {formatted} (Commit: {link})")
        lines.append("")

    for section in sections:
        sid = section["id"]
        content = section_contents.get(sid)
        if content is None:
            continue
        lines.append(f"## {escape_mdx_text(section['title'])}")
        lines.append("")
        lines.append(content)
        lines.append("")

    lines.append("---")

    wiki_path = output_dir / "wiki.mdx"
    wiki_path.write_text("\n".join(lines))
    return wiki_path


def validate_mdx_optional(wiki_path: Path) -> None:
    """Best-effort MDX validation via Node.js (skipped if Node/MDX is unavailable).

    Looks for scripts/validate_mdx.mjs next to this file. The validator script
    relies on an installed ``@mdx-js/mdx`` package; when it's absent we log a
    warning instead of failing the wiki generation.
    """
    validator = Path(__file__).parent / "validate_mdx.mjs"
    if not validator.exists() or shutil.which("node") is None:
        log.info("Skipping MDX validation (validator or Node.js not available)")
        return
    try:
        proc = subprocess.run(
            ["node", str(validator)],
            input=wiki_path.read_text(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            log.warning("MDX validation reported issues:\n%s", proc.stdout or proc.stderr)
        else:
            log.info("✓ MDX validation passed")
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("MDX validation failed to run: %s", exc)


def build_static_optional(output_dir: Path) -> None:
    """Emit a self-contained ``<output-dir>/index.html`` via build_static.py.

    Imported lazily (and behind a try/except) so a broken or missing builder
    never breaks wiki generation itself.
    """
    builder = Path(__file__).parent / "build_static.py"
    if not builder.exists():
        log.info("Skipping static index.html (build_static.py not found)")
        return
    try:
        sys.path.insert(0, str(builder.parent))
        try:
            import build_static  # type: ignore[import-not-found]
        finally:
            sys.path.pop(0)
        target = build_static.build(output_dir)
        log.info("✓ Wrote static viewer -> %s", target)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Static viewer emission failed: %s", exc)


def publish_git_optional(args: argparse.Namespace, output_dir: Path) -> bool:
    """Optional Git-backed Astro publish step.

    Only runs when ``--publish-git`` is set. Clones a host Astro
    repository, writes the new wiki entry, and pushes. The default
    behaviour (local filesystem output) is unchanged when this flag is
    omitted, so this step is purely additive and any failure here
    leaves the local ``wiki.mdx``/``index.html`` deliverables intact.

    Returns ``True`` when the publish completed (or was a successful
    dry run), ``False`` when build validation was skipped due to
    missing tooling so the orchestrator can propagate exit code 3.
    """
    if not args.publish_git:
        return True
    publisher = Path(__file__).parent / "publish_git.py"
    if not publisher.exists():
        raise RuntimeError(
            f"--publish-git set but {publisher} is missing from the skill"
        )
    sys.path.insert(0, str(publisher.parent))
    try:
        import publish_git  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    work_dir = (
        Path(args.wiki_work_dir).expanduser().resolve()
        if args.wiki_work_dir
        else None
    )
    result = publish_git.publish(
        output_dir=output_dir,
        wiki_repo=args.wiki_repo,
        branch=args.wiki_branch,
        slug=args.wiki_slug,
        push=not args.no_push,
        work_dir=work_dir,
        keep_work_dir=args.keep_wiki_work_dir,
        skip_build_validation=args.skip_build_validation,
    )
    # ``tooling_missing`` is the dedicated signal from ``publish()`` for
    # "node/npm not on PATH and a push was requested".  ``--no-push`` and
    # ``--skip-build-validation`` runs also flip ``validation_skipped``,
    # but they are not degraded outcomes from the user's perspective, so
    # branching on the inferred combination would over-report.
    if result.tooling_missing:
        # ``publish()`` bails before staging in this branch, so the
        # entry is only on the working tree.  Manual recovery requires
        # ``git add`` + ``git commit`` + ``git push`` after the build;
        # scope the ``git add`` to the slug directory so validation
        # artifacts (``node_modules/``, ``package-lock.json``, ``.astro/``,
        # ``dist/``) don't end up in the publish commit.
        slug_path = f"src/content/wikis/{result.slug}"
        log.warning(
            "Published %s locally to %s but DID NOT push: build "
            "validation %s. Install Node.js, then in the host clone "
            "run: `npm install && npm run build && git add -- %s && "
            "git commit -m 'deep-wiki: update %s' && git push origin %s`. "
            "Or re-run with --skip-build-validation to bypass.",
            result.slug,
            result.entry_path,
            result.validation_skipped_reason,
            slug_path,
            result.slug,
            result.branch,
        )
        return False
    log.info(
        "Published %s -> %s (commit=%s pushed=%s)",
        result.slug,
        result.entry_path,
        result.commit_sha or "<no-change>",
        result.pushed,
    )
    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def generate_wiki(args: argparse.Namespace) -> tuple[Path, bool]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = Path(__file__).parent.parent / "prompts"

    workspace_dir = args.workspace_dir or tempfile.mkdtemp(prefix="deep_wiki_workspace_")
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="deep_wiki_cache_")
    cleanup_workspace = args.workspace_dir is None and not args.no_cleanup
    cleanup_cache = args.cache_dir is None and not args.no_cleanup

    log.info("Workspace: %s", workspace_dir)
    log.info("Cache: %s", cache_dir)
    log.info("Output:  %s", output_dir)

    started = time.monotonic()
    try:
        ws = Path(workspace_dir)
        ws.mkdir(parents=True, exist_ok=True)
        if not any(ws.iterdir()):
            clone_repo(args.repo_url, workspace_dir)
        else:
            log.info("Reusing existing workspace contents at %s", workspace_dir)

        common = dict(
            workspace_dir=workspace_dir,
            cache_dir=cache_dir,
            prompts_dir=prompts_dir,
            output_dir=output_dir,
            model=args.model,
            timeout=args.timeout,
            api_url=args.api_url,
            auggie_bin=args.auggie_bin,
        )

        log.info("Step 1/3: repo metadata")
        metadata = generate_metadata(repo_url=args.repo_url, **common)

        log.info("Step 2/3: wiki structure")
        structure = generate_structure(**common)

        log.info("Step 3/3: %d sections", len(structure["sections"]))
        section_contents = generate_sections(sections=structure["sections"], **common)

        wiki_path = assemble_wiki(
            output_dir=output_dir,
            repo_url=args.repo_url,
            structure=structure,
            section_contents=section_contents,
            metadata=metadata,
        )

        if not args.skip_validate:
            validate_mdx_optional(wiki_path)

        if not args.no_static:
            build_static_optional(output_dir)

        publish_ok = publish_git_optional(args, output_dir)

        elapsed = time.monotonic() - started
        log.info("✓ Wiki generated in %.1fs -> %s", elapsed, wiki_path)
        return wiki_path, publish_ok
    finally:
        if cleanup_workspace:
            log.info("Cleaning up workspace %s", workspace_dir)
            shutil.rmtree(workspace_dir, ignore_errors=True)
        if cleanup_cache:
            log.info("Cleaning up cache %s", cache_dir)
            shutil.rmtree(cache_dir, ignore_errors=True)



def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate_wiki",
        description=(
            "Generate a DeepWiki-style MDX guide for a repository by orchestrating "
            "the auggie CLI through metadata, structure, and per-section steps."
        ),
    )
    p.add_argument(
        "repo_url",
        help="Repository URL to clone (e.g. https://github.com/pallets/flask)",
    )
    p.add_argument(
        "--output-dir",
        "-o",
        required=True,
        help="Directory to write wiki.mdx, sections/, repo_metadata.json, wiki_structure.json",
    )
    p.add_argument(
        "--model",
        "-m",
        default=os.environ.get("AUGGIE_DEEP_WIKI_MODEL", DEFAULT_MODEL),
        help=f"Model name passed to auggie --model (default: {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-step auggie timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("AUGMENT_API_URL", DEFAULT_API_URL),
        help=(
            "Augment API URL. Default: empty, in which case auggie reads the "
            "tenantURL from the session file. Set to override (e.g. for "
            "staging-shard-0.api.augmentcode.com)."
        ),
    )
    p.add_argument(
        "--auggie-bin",
        default=os.environ.get("AUGMENT_AGENT_BINARY_PATH", "auggie"),
        help="Path to the auggie binary (default: 'auggie' in PATH)",
    )
    p.add_argument(
        "--workspace-dir",
        default=None,
        help="Reuse an existing workspace directory; if empty, the repo will be cloned into it",
    )
    p.add_argument(
        "--cache-dir",
        default=None,
        help="Reuse an existing auggie cache directory across steps",
    )
    p.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep workspace and cache temp dirs after generation (debugging)",
    )
    p.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip the optional Node-based MDX validation step",
    )
    p.add_argument(
        "--no-static",
        action="store_true",
        help=(
            "Skip emitting a self-contained <output-dir>/index.html viewer. "
            "By default the generator bundles wiki.mdx into a static HTML file "
            "so the result can be opened directly via file:// without preview.py."
        ),
    )
    p.add_argument(
        "--publish-git",
        action="store_true",
        help=(
            "Optional: clone a Git-backed Astro site repo and append "
            "this wiki to it (then push). The local filesystem output "
            "is unchanged when this flag is omitted. Requires --wiki-repo "
            "or $DEEP_WIKIS_GIT_REPO; uses $GITHUB_TOKEN for HTTPS auth "
            "when present, otherwise falls back to git's default "
            "credentials (SSH key, credential helper)."
        ),
    )
    p.add_argument(
        "--wiki-repo",
        default=os.environ.get("DEEP_WIKIS_GIT_REPO"),
        help=(
            "URL of the host Astro repo to publish into (e.g. "
            "https://github.com/<org>/deep-wikis.git). Required when "
            "--publish-git is set; can also be set via "
            "$DEEP_WIKIS_GIT_REPO."
        ),
    )
    p.add_argument(
        "--wiki-branch",
        default="main",
        help="Branch on the host repo to push to (default: main)",
    )
    p.add_argument(
        "--wiki-slug",
        default=None,
        help=(
            "Override the URL slug for this wiki "
            "(path /wikis/<slug>/). Defaults to <owner>-<repo>."
        ),
    )
    p.add_argument(
        "--wiki-work-dir",
        default=None,
        help=(
            "Persistent clone directory for the host repo (default: "
            "ephemeral temp dir, removed on success)."
        ),
    )
    p.add_argument(
        "--keep-wiki-work-dir",
        action="store_true",
        help="Keep the host-repo clone after a successful run (debugging)",
    )
    p.add_argument(
        "--no-push",
        action="store_true",
        help="Commit locally only; do not push the host repo (dry-run)",
    )
    p.add_argument(
        "--skip-build-validation",
        action="store_true",
        help=(
            "Skip the pre-push `astro build` validation against the host "
            "repo clone. Use only when CI does the same check; otherwise "
            "broken MDX/YAML can land on the deployed site."
        ),
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if shutil.which(args.auggie_bin) is None and not Path(args.auggie_bin).exists():
        log.error(
            "auggie binary not found: %s. Install Auggie CLI or pass --auggie-bin.",
            args.auggie_bin,
        )
        return 2
    if shutil.which("git") is None:
        log.error("git is required but not found in PATH")
        return 2

    try:
        _, publish_ok = generate_wiki(args)
    except KeyboardInterrupt:
        log.error("Interrupted")
        return 130
    except Exception as exc:
        log.error("Wiki generation failed: %s", exc)
        if args.verbose:
            raise
        return 1
    # Exit code 3 mirrors publish_git's: the wiki was generated locally
    # but the host repo could not be updated because build-validation
    # tooling (node/npm) was missing.  Distinguishes "fix your env" from
    # "everything succeeded" (0) or "hard failure" (1).
    if not publish_ok:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
