#!/usr/bin/env python3
"""Publish a generated deep-wiki output to Vercel via Astro.

This is the optional companion to ``generate_wiki.py``. The default skill
behaviour (writing ``wiki.mdx`` + ``index.html`` to the local filesystem)
is unchanged; this module only runs when the user (or ``generate_wiki.py
--publish-vercel``) asks for it.

Pipeline:
  1. Verify the Vercel CLI is installed and the user is logged in
     (``vercel whoami``). Abort with a clear error if either check fails.
  2. Verify Node.js / npm are installed.
  3. Ensure an Astro site directory exists. If missing, copy the
     ``astro-template/`` shipped with this skill into ``--vercel-site-dir``
     and run ``npm install`` once.
  4. Convert ``<output-dir>/wiki.mdx`` plus ``repo_metadata.json`` into a
     content-collection entry at
     ``<vercel-site-dir>/src/content/wikis/<slug>/index.mdx`` with valid
     Astro frontmatter.
  5. Run ``vercel deploy [--prod]`` from the Astro site dir and print the
     resulting deployment URL.

The script can be invoked standalone or imported by ``generate_wiki.py``
via ``publish(output_dir=..., site_dir=..., slug=..., prod=...)``.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("auggie-deep-wiki.publish")

DEFAULT_SITE_DIR = Path.home() / ".augment" / "deep-wiki-site"
SKILL_ROOT = Path(__file__).resolve().parent.parent
ASTRO_TEMPLATE_DIR = SKILL_ROOT / "astro-template"


class PublishError(RuntimeError):
    """Raised when the publish pipeline cannot continue."""


@dataclass
class PublishResult:
    site_dir: Path
    slug: str
    entry_path: Path
    deployment_url: str | None


# ---------------------------------------------------------------------------
# subprocess helpers
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
# preflight checks
# ---------------------------------------------------------------------------
def check_vercel_cli() -> str:
    """Verify ``vercel`` is on PATH and the user is authenticated.

    Raises ``PublishError`` with an actionable message if either check
    fails. Returns the authenticated username on success.
    """
    bin_path = shutil.which("vercel")
    if bin_path is None:
        raise PublishError(
            "Vercel CLI not found on PATH. Install it with `npm i -g vercel` "
            "and authenticate with `vercel login`, then re-run."
        )
    try:
        proc = _run(["vercel", "whoami"], capture=True, check=False, timeout=30)
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise PublishError(f"Failed to invoke vercel: {exc}") from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or not out.strip():
        raise PublishError(
            "Vercel CLI is installed but not authenticated. Run "
            "`vercel login` and re-run the skill with --publish-vercel."
        )
    user = out.strip().splitlines()[-1].strip()
    log.info("Authenticated to Vercel as %s", user)
    return user


def check_node_npm() -> None:
    if shutil.which("node") is None or shutil.which("npm") is None:
        raise PublishError(
            "Node.js + npm are required to build the Astro site. Install "
            "Node.js 20+ and re-run."
        )


# ---------------------------------------------------------------------------
# slug + frontmatter helpers
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

    Handles strings (always quoted), numbers, booleans, and lists of
    strings. Anything else falls back to a JSON-encoded string.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_yaml_scalar(v) for v in value)
        return f"[{items}]"
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _strip_existing_frontmatter(mdx: str) -> str:
    """Drop the bookend ``---`` lines (if any) from ``wiki.mdx``.

    ``generate_wiki.assemble_wiki`` brackets the document with a leading
    ``---`` line and a trailing ``---`` line. We treat them as bookends
    (not real YAML frontmatter, which would put key/value pairs between
    them) and strip whichever ones are present so we can wrap the body
    with valid Astro frontmatter instead.
    """
    lines = mdx.splitlines()
    # Trim a leading ``---`` line (and any surrounding blank lines).
    while lines and lines[0].strip() == "":
        lines.pop(0)
    if lines and lines[0].strip() == "---":
        lines.pop(0)
    # Trim a trailing ``---`` line (and any surrounding blank lines).
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
# site scaffolding
# ---------------------------------------------------------------------------
def ensure_site(site_dir: Path) -> bool:
    """Create the Astro site at ``site_dir`` from the bundled template.

    Returns ``True`` when the site was freshly scaffolded (so the caller
    knows to run ``npm install``); ``False`` when an existing site is
    being reused.
    """
    if (site_dir / "package.json").exists():
        log.info("Reusing Astro site at %s", site_dir)
        return False
    if not ASTRO_TEMPLATE_DIR.exists():
        raise PublishError(
            f"Astro template directory missing: {ASTRO_TEMPLATE_DIR}"
        )
    log.info("Scaffolding Astro site -> %s", site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    for src in ASTRO_TEMPLATE_DIR.rglob("*"):
        rel = src.relative_to(ASTRO_TEMPLATE_DIR)
        dst = site_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return True


def npm_install_if_needed(site_dir: Path, force: bool = False) -> None:
    if not force and (site_dir / "node_modules").exists():
        log.info("Skipping npm install (node_modules present)")
        return
    log.info("Running npm install in %s", site_dir)
    _run(["npm", "install", "--no-fund", "--no-audit"], cwd=site_dir, timeout=600)


# ---------------------------------------------------------------------------
# content + deploy
# ---------------------------------------------------------------------------
def write_entry(
    *,
    site_dir: Path,
    slug: str,
    output_dir: Path,
) -> Path:
    """Materialize the wiki at ``site_dir/src/content/wikis/<slug>/index.mdx``."""
    wiki_path = output_dir / "wiki.mdx"
    if not wiki_path.exists():
        raise PublishError(f"Expected wiki.mdx at {wiki_path}; nothing to publish.")
    metadata: dict[str, Any] = {}
    meta_path = output_dir / "repo_metadata.json"
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text())
        except json.JSONDecodeError as exc:
            log.warning("repo_metadata.json is not valid JSON: %s", exc)
    structure: dict[str, Any] | None = None
    struct_path = output_dir / "wiki_structure.json"
    if struct_path.exists():
        try:
            structure = json.loads(struct_path.read_text())
        except json.JSONDecodeError as exc:
            log.warning("wiki_structure.json is not valid JSON: %s", exc)

    content = build_entry_mdx(
        wiki_mdx=wiki_path.read_text(),
        metadata=metadata,
        structure=structure,
    )
    target_dir = site_dir / "src" / "content" / "wikis" / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "index.mdx"
    target.write_text(content)
    log.info("Wrote content entry -> %s", target)
    return target


_URL_RE = re.compile(r"https?://[^\s]+")


def vercel_deploy(site_dir: Path, *, prod: bool, yes: bool) -> str | None:
    """Run ``vercel deploy`` and return the first URL printed by the CLI."""
    cmd = ["vercel", "deploy"]
    if prod:
        cmd.append("--prod")
    if yes:
        cmd.append("--yes")
    log.info("Deploying to Vercel (%s)…", "prod" if prod else "preview")
    proc = _run(cmd, cwd=site_dir, capture=True, timeout=1800)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    sys.stdout.write(out)
    match = _URL_RE.search(proc.stdout or "")
    if match:
        return match.group(0).rstrip(".,")
    return None


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def publish(
    *,
    output_dir: Path,
    site_dir: Path | None = None,
    slug: str | None = None,
    prod: bool = False,
    yes: bool = True,
    skip_install: bool = False,
    skip_deploy: bool = False,
) -> PublishResult:
    """Run the full publish pipeline. Idempotent across repeat invocations.

    Args:
        output_dir: Path to a generate_wiki.py output directory containing
            ``wiki.mdx`` and (optionally) ``repo_metadata.json``.
        site_dir: Persistent Astro site directory. Defaults to
            ``~/.augment/deep-wiki-site``. Reused across multiple wikis.
        slug: Override for the URL slug (``/wikis/<slug>/``). Auto-derived
            from the repo metadata when not supplied.
        prod: Deploy to the production environment instead of a preview.
        yes: Pass ``--yes`` to ``vercel deploy`` so the CLI skips
            interactive prompts when a project is already linked.
        skip_install: Skip the ``npm install`` step (assume node_modules
            is already populated).
        skip_deploy: Stop after writing the content entry and (optionally)
            running ``npm install``; do not invoke ``vercel deploy``.
    """
    output_dir = Path(output_dir).resolve()
    if not output_dir.exists():
        raise PublishError(f"Output dir does not exist: {output_dir}")

    site_dir = Path(site_dir or DEFAULT_SITE_DIR).resolve()

    check_vercel_cli()
    check_node_npm()

    fresh = ensure_site(site_dir)
    if not skip_install:
        npm_install_if_needed(site_dir, force=fresh)

    metadata: dict[str, Any] = {}
    meta_path = output_dir / "repo_metadata.json"
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            metadata = {}
    final_slug = slug or derive_slug(metadata.get("repo_url"), metadata)
    final_slug = _SLUG_RE.sub("-", final_slug.lower()).strip("-") or "wiki"

    entry_path = write_entry(site_dir=site_dir, slug=final_slug, output_dir=output_dir)

    deployment_url: str | None = None
    if not skip_deploy:
        deployment_url = vercel_deploy(site_dir, prod=prod, yes=yes)
        if deployment_url:
            log.info("✓ Deployed: %s", deployment_url)
            log.info("  Wiki URL: %s/wikis/%s/", deployment_url, final_slug)
        else:
            log.warning("Could not detect a deployment URL in vercel output")

    return PublishResult(
        site_dir=site_dir,
        slug=final_slug,
        entry_path=entry_path,
        deployment_url=deployment_url,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="publish_vercel",
        description=(
            "Publish a generate_wiki.py output directory to Vercel via Astro. "
            "Reuses (and creates on first run) a single Astro site at "
            "--site-dir so multiple wikis live under one Vercel project."
        ),
    )
    p.add_argument("output_dir", help="Path to a generate_wiki.py output dir")
    p.add_argument(
        "--site-dir",
        default=str(DEFAULT_SITE_DIR),
        help=f"Astro site directory (default: {DEFAULT_SITE_DIR})",
    )
    p.add_argument(
        "--slug",
        default=None,
        help="Override the URL slug (default: derived from repo_metadata.json)",
    )
    p.add_argument(
        "--prod",
        action="store_true",
        help="Deploy to production (default: preview deployment)",
    )
    p.add_argument(
        "--no-yes",
        dest="yes",
        action="store_false",
        help="Do not pass --yes to `vercel deploy`",
    )
    p.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip `npm install`",
    )
    p.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Write the content entry but do not run `vercel deploy`",
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
        publish(
            output_dir=Path(args.output_dir),
            site_dir=Path(args.site_dir),
            slug=args.slug,
            prod=args.prod,
            yes=args.yes,
            skip_install=args.skip_install,
            skip_deploy=args.skip_deploy,
        )
    except PublishError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

