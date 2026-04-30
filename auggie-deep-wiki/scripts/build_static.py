#!/usr/bin/env python3
"""Build a self-contained ``index.html`` viewer inside a deep-wiki output dir.

Reads ``<output-dir>/wiki.mdx`` and embeds it into a copy of the bundled
viewer template (``preview/index.html``) so the result can be opened
directly via ``file://`` or shipped inside any archive without the
``preview.py`` server.

Usage:
    python3 build_static.py <output-dir> [--template PATH] [--output PATH]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SKILL_DIR / "preview" / "index.html"

# Matches the empty source tag the template ships with. We allow either an
# already-empty tag or one previously populated by a prior build_static run.
SOURCE_TAG_RE = re.compile(
    r'<script id="wiki-source" type="text/markdown">.*?</script>',
    re.DOTALL,
)


def escape_for_script(mdx: str) -> str:
    """Escape sequences that would terminate the host ``<script>`` tag.

    Per the HTML spec, only ``</script>`` (case-insensitive) ends a script
    element's raw text content; ``<!--`` / ``-->`` only switch tokenizer
    states without actually terminating, and escaping them here would
    corrupt Mermaid arrows (``-->``) and HTML comments inside the wiki.
    Backslashes and quotes pass through untouched because the body is read
    via ``textContent``, not parsed as JavaScript.
    """
    return re.sub(r"</(script)", r"<\\/\1", mdx, flags=re.IGNORECASE)


def build(
    output_dir: Path,
    *,
    template: Path = DEFAULT_TEMPLATE,
    output_file: Path | None = None,
) -> Path:
    if not output_dir.is_dir():
        raise SystemExit(f"output dir not found: {output_dir}")
    wiki = output_dir / "wiki.mdx"
    if not wiki.is_file():
        raise SystemExit(f"wiki.mdx not found in {output_dir}")
    if not template.is_file():
        raise SystemExit(f"viewer template not found: {template}")

    mdx = wiki.read_text(encoding="utf-8")
    html = template.read_text(encoding="utf-8")

    if not SOURCE_TAG_RE.search(html):
        raise SystemExit(
            "viewer template missing the <script id=\"wiki-source\"> placeholder; "
            "is the template up to date?"
        )

    embedded = (
        '<script id="wiki-source" type="text/markdown">\n'
        + escape_for_script(mdx)
        + "\n</script>"
    )
    bundled = SOURCE_TAG_RE.sub(lambda _m: embedded, html, count=1)

    target = output_file or (output_dir / "index.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(bundled, encoding="utf-8")
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Bundle wiki.mdx into a self-contained index.html viewer "
            "(opens directly via file:// without the preview server)."
        ),
    )
    p.add_argument(
        "output_dir",
        help="Path to a deep-wiki output directory (must contain wiki.mdx).",
    )
    p.add_argument(
        "--template",
        default=str(DEFAULT_TEMPLATE),
        help="Path to the viewer template HTML (default: skill's preview/index.html).",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Destination HTML file (default: <output-dir>/index.html).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target = build(
        Path(args.output_dir).expanduser().resolve(),
        template=Path(args.template).expanduser().resolve(),
        output_file=Path(args.output).expanduser().resolve() if args.output else None,
    )
    size_kb = target.stat().st_size / 1024
    print(f"Wrote {target} ({size_kb:.1f} KB)")
    print(f"Open with: open {target}    # macOS")
    print(f"           xdg-open {target}    # Linux")
    return 0


if __name__ == "__main__":
    sys.exit(main())
