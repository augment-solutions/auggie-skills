"""Unit tests for ``publish_vercel`` pure helpers.

Run with::

    python3 -m pytest scripts/test_publish_vercel.py -v

Covers:
- ``derive_slug``           — metadata-first, URL fallback, default.
- ``_strip_existing_frontmatter`` — assembler bookend handling.
- ``build_entry_mdx``       — Astro frontmatter assembly.
- ``ensure_site``           — template copy + reuse.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``scripts/`` importable when pytest is invoked from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_vercel as pv  # noqa: E402


# ---------------------------------------------------------------------------
# derive_slug
# ---------------------------------------------------------------------------
class TestDeriveSlug:
    def test_owner_and_name_from_metadata(self):
        assert (
            pv.derive_slug(None, {"owner": "Pallets", "name": "Click"})
            == "pallets-click"
        )

    def test_repo_name_alias(self):
        assert (
            pv.derive_slug(None, {"owner": "pallets", "repo_name": "flask"})
            == "pallets-flask"
        )

    def test_name_only(self):
        assert pv.derive_slug(None, {"name": "MyRepo"}) == "myrepo"

    def test_url_with_git_suffix(self):
        assert (
            pv.derive_slug("https://github.com/pallets/click.git", None)
            == "pallets-click"
        )

    def test_url_with_trailing_slash(self):
        assert (
            pv.derive_slug("https://github.com/pallets/click/", None)
            == "pallets-click"
        )

    def test_metadata_wins_over_url(self):
        assert (
            pv.derive_slug(
                "https://github.com/other/repo",
                {"owner": "pallets", "name": "click"},
            )
            == "pallets-click"
        )

    def test_special_chars_collapsed(self):
        assert (
            pv.derive_slug(None, {"owner": "Foo Bar", "name": "Baz_Qux!"})
            == "foo-bar-baz-qux"
        )

    def test_default_when_nothing_provided(self):
        assert pv.derive_slug(None, None) == "wiki"
        assert pv.derive_slug("", {}) == "wiki"


# ---------------------------------------------------------------------------
# _strip_existing_frontmatter
# ---------------------------------------------------------------------------
class TestStripExistingFrontmatter:
    def test_strips_bookend_dashes(self):
        mdx = "---\n# Title\n\nBody text\n---\n"
        assert pv._strip_existing_frontmatter(mdx) == "# Title\n\nBody text"

    def test_no_frontmatter_passes_through(self):
        mdx = "# Title\n\nBody"
        assert pv._strip_existing_frontmatter(mdx) == "# Title\n\nBody"

    def test_only_leading_bookend(self):
        assert pv._strip_existing_frontmatter("---\n# Title") == "# Title"

    def test_handles_blank_lines_around_bookends(self):
        mdx = "\n\n---\n\n# Title\n\n---\n\n"
        assert pv._strip_existing_frontmatter(mdx) == "# Title"


# ---------------------------------------------------------------------------
# build_entry_mdx
# ---------------------------------------------------------------------------
class TestBuildEntryMdx:
    def test_full_metadata_roundtrip(self):
        out = pv.build_entry_mdx(
            wiki_mdx="---\n# Flask\n\nHello\n---\n",
            metadata={
                "name": "Flask",
                "owner": "pallets",
                "repo_url": "https://github.com/pallets/flask",
                "github_description": "A microframework",
                "commit_date": "2026-01-01 12:00:00 +0000",
                "commit_hash": "abc1234567",
                "commit_hash_short": "abc1234",
                "github_stars": 65000,
                "github_language": "Python",
                "github_topics": ["python", "web"],
            },
            structure=None,
        )
        assert out.startswith("---\n")
        assert 'title: "Flask"' in out
        assert 'description: "A microframework"' in out
        assert 'repo_url: "https://github.com/pallets/flask"' in out
        assert "stars: 65000" in out
        assert 'topics: ["python", "web"]' in out
        # Body present, original bookends gone.
        assert "# Flask" in out
        assert "Hello" in out
        assert out.count("---\n") == 2  # opening + closing frontmatter only

    def test_title_falls_back_to_repo(self):
        out = pv.build_entry_mdx(
            wiki_mdx="# X", metadata={"repo_name": "thing"}, structure=None
        )
        assert 'title: "thing"' in out

    def test_structure_overrides_metadata_title(self):
        out = pv.build_entry_mdx(
            wiki_mdx="# X",
            metadata={"name": "fallback"},
            structure={"title": "Override"},
        )
        assert 'title: "Override"' in out


# ---------------------------------------------------------------------------
# ensure_site
# ---------------------------------------------------------------------------
class TestEnsureSite:
    def test_fresh_scaffold(self, tmp_path: Path):
        target = tmp_path / "site"
        assert pv.ensure_site(target) is True
        assert (target / "package.json").is_file()
        assert (target / "src" / "content.config.ts").is_file()
        assert (target / "src" / "pages" / "index.astro").is_file()

    def test_reuses_existing_site(self, tmp_path: Path):
        target = tmp_path / "site"
        pv.ensure_site(target)
        # Mutate a file to confirm reuse leaves it untouched.
        marker = target / "package.json"
        marker.write_text("// reused\n")
        assert pv.ensure_site(target) is False
        assert marker.read_text() == "// reused\n"
