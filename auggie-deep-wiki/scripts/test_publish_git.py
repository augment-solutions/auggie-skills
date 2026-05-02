"""Unit tests for ``publish_git`` pure helpers.

Run with::

    python3 -m pytest scripts/test_publish_git.py -v

Covers:
- ``derive_slug``           — metadata-first, URL fallback, default.
- ``_strip_existing_frontmatter`` — assembler bookend handling.
- ``build_entry_mdx``       — Astro frontmatter assembly with control-char escaping.
- ``_yaml_scalar``          — quoting, control chars, lists, bools.
- ``_git_base`` / ``_resolve_token`` — auth-header injection.
- ``write_entry``           — atomic content-collection entry replacement.
- ``publish``               — end-to-end with mocked git/clone (no network).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_git as pg  # noqa: E402


# ---------------------------------------------------------------------------
# derive_slug (mirrors test_publish_vercel.py coverage)
# ---------------------------------------------------------------------------
class TestDeriveSlug:
    def test_owner_and_name_from_metadata(self):
        assert pg.derive_slug(None, {"owner": "Pallets", "name": "Click"}) == "pallets-click"

    def test_repo_name_alias(self):
        assert pg.derive_slug(None, {"owner": "pallets", "repo_name": "flask"}) == "pallets-flask"

    def test_name_only(self):
        assert pg.derive_slug(None, {"name": "MyRepo"}) == "myrepo"

    def test_url_with_git_suffix(self):
        assert pg.derive_slug("https://github.com/pallets/click.git", None) == "pallets-click"

    def test_url_with_trailing_slash(self):
        assert pg.derive_slug("https://github.com/pallets/click/", None) == "pallets-click"

    def test_metadata_wins_over_url(self):
        assert (
            pg.derive_slug(
                "https://github.com/other/repo",
                {"owner": "Pallets", "name": "Click"},
            )
            == "pallets-click"
        )

    def test_special_chars_collapsed(self):
        assert pg.derive_slug(None, {"owner": "ACME!", "name": "My Repo"}) == "acme-my-repo"

    def test_default_when_nothing_provided(self):
        assert pg.derive_slug(None, None) == "wiki"


# ---------------------------------------------------------------------------
# _yaml_scalar
# ---------------------------------------------------------------------------
class TestYamlScalar:
    def test_string_quoted(self):
        assert pg._yaml_scalar("hello") == '"hello"'

    def test_quotes_escaped(self):
        assert pg._yaml_scalar('say "hi"') == r'"say \"hi\""'

    def test_backslash_escaped(self):
        assert pg._yaml_scalar(r"path\to\file") == r'"path\\to\\file"'

    def test_newline_escaped(self):
        assert pg._yaml_scalar("line1\nline2") == r'"line1\nline2"'

    def test_carriage_return_escaped(self):
        assert pg._yaml_scalar("a\r\nb") == r'"a\r\nb"'

    def test_tab_escaped(self):
        assert pg._yaml_scalar("a\tb") == r'"a\tb"'

    def test_int_passthrough(self):
        assert pg._yaml_scalar(42) == "42"

    def test_bool_lowercased(self):
        assert pg._yaml_scalar(True) == "true"
        assert pg._yaml_scalar(False) == "false"

    def test_list_of_strings(self):
        assert pg._yaml_scalar(["a", "b"]) == '["a", "b"]'


# ---------------------------------------------------------------------------
# _strip_existing_frontmatter
# ---------------------------------------------------------------------------
class TestStripExistingFrontmatter:
    def test_strips_bookend_dashes(self):
        body = "---\n# Title\n\nText\n---\n"
        assert pg._strip_existing_frontmatter(body) == "# Title\n\nText"

    def test_no_frontmatter_passes_through(self):
        body = "# Title\n\nText\n"
        assert pg._strip_existing_frontmatter(body) == "# Title\n\nText"

    def test_only_leading_bookend(self):
        body = "---\n# Title\n\nText\n"
        assert pg._strip_existing_frontmatter(body) == "# Title\n\nText"


# ---------------------------------------------------------------------------
# build_entry_mdx
# ---------------------------------------------------------------------------
class TestBuildEntryMdx:
    def test_full_metadata_roundtrip(self):
        out = pg.build_entry_mdx(
            wiki_mdx="---\n# Repo\n\nbody\n---\n",
            metadata={
                "name": "Click",
                "github_description": "A simple framework",
                "repo_url": "https://github.com/pallets/click",
                "github_stars": 16000,
                "github_topics": ["python", "cli"],
            },
            structure={"title": "Click Deep Wiki"},
        )
        assert out.startswith("---\n")
        assert 'title: "Click Deep Wiki"' in out
        assert 'description: "A simple framework"' in out
        assert 'repo_url: "https://github.com/pallets/click"' in out
        assert "stars: 16000" in out
        assert 'topics: ["python", "cli"]' in out
        assert "# Repo" in out



    def test_multiline_description_escaped(self):
        out = pg.build_entry_mdx(
            wiki_mdx="# X",
            metadata={"name": "thing", "github_description": "first\nsecond"},
            structure=None,
        )
        assert r'description: "first\nsecond"' in out
        assert out.count("---\n") == 2  # only the bookend dashes


# ---------------------------------------------------------------------------
# auth header / token resolution
# ---------------------------------------------------------------------------
class TestGitBase:
    def test_no_token_means_plain_git(self):
        assert pg._git_base(None) == ["git"]

    def test_token_injected_via_header(self):
        cmd = pg._git_base("ghp_secret")
        assert cmd[0] == "git"
        assert "-c" in cmd
        idx = cmd.index("-c")
        assert cmd[idx + 1] == "http.extraHeader=Authorization: Bearer ghp_secret"


class TestResolveToken:
    def test_github_token_wins(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "primary")
        monkeypatch.setenv("GH_TOKEN", "secondary")
        assert pg._resolve_token() == "primary"

    def test_gh_token_fallback(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "fallback")
        assert pg._resolve_token() == "fallback"

    def test_empty_value_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "  ")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert pg._resolve_token() is None


# ---------------------------------------------------------------------------
# write_entry — atomic replacement
# ---------------------------------------------------------------------------
class TestWriteEntry:
    def test_creates_directory_and_index_mdx(self, tmp_path):
        entry = pg.write_entry(
            tmp_path,
            "pallets-click",
            wiki_mdx="# Click\n\nbody",
            metadata={"name": "click"},
            structure=None,
        )
        rel = entry.relative_to(tmp_path)
        assert rel == Path("src/content/wikis/pallets-click/index.mdx")
        text = entry.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert 'title: "click"' in text
        assert "# Click" in text

    def test_replaces_existing_dir_atomically(self, tmp_path):
        target = tmp_path / "src" / "content" / "wikis" / "pallets-click"
        target.mkdir(parents=True)
        # Stale auxiliary file from a previous run.
        (target / "stale.png").write_bytes(b"\x89PNG")
        (target / "index.mdx").write_text("OLD")

        pg.write_entry(
            tmp_path,
            "pallets-click",
            wiki_mdx="# New\n",
            metadata={"name": "click"},
            structure=None,
        )

        # Stale file was wiped, fresh entry exists.
        assert not (target / "stale.png").exists()
        assert "# New" in (target / "index.mdx").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# publish() — end-to-end with mocked git invocations
# ---------------------------------------------------------------------------
class TestPublish:
    def _make_output(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "wiki.mdx").write_text("---\n# T\n\nbody\n---\n")
        (out / "repo_metadata.json").write_text(
            '{"owner": "pallets", "name": "click", '
            '"repo_url": "https://github.com/pallets/click"}'
        )
        return out

    def test_no_repo_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEP_WIKIS_GIT_REPO", raising=False)
        out = self._make_output(tmp_path)
        with pytest.raises(pg.PublishError, match="No host repo configured"):
            pg.publish(output_dir=out, push=False)

    def test_uses_env_var_when_no_arg(self, tmp_path, monkeypatch):
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        captured: dict[str, Any] = {}

        def fake_clone(repo_url, branch, work_dir, *, token):
            captured["repo_url"] = repo_url
            captured["branch"] = branch
            (work_dir / "src" / "content" / "wikis").mkdir(parents=True)

        monkeypatch.setattr(pg, "clone_host_repo", fake_clone)
        monkeypatch.setattr(
            pg, "commit_and_push", lambda *a, **kw: ("abc1234", False)
        )

        result = pg.publish(output_dir=out, push=False)
        assert captured["repo_url"] == "https://github.com/x/y.git"
        assert captured["branch"] == "main"
        assert result.slug == "pallets-click"
        assert result.commit_sha == "abc1234"
        assert result.pushed is False

    def test_explicit_arg_wins_over_env(self, tmp_path, monkeypatch):
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/env.git")
        captured: dict[str, Any] = {}

        def fake_clone(repo_url, branch, work_dir, *, token):
            captured["repo_url"] = repo_url
            (work_dir / "src" / "content" / "wikis").mkdir(parents=True)

        monkeypatch.setattr(pg, "clone_host_repo", fake_clone)
        monkeypatch.setattr(
            pg, "commit_and_push", lambda *a, **kw: (None, False)
        )

        pg.publish(
            output_dir=out,
            wiki_repo="https://github.com/x/explicit.git",
            push=False,
        )
        assert captured["repo_url"] == "https://github.com/x/explicit.git"

    def test_missing_wiki_mdx_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(pg.PublishError, match="Missing input"):
            pg.publish(
                output_dir=empty,
                wiki_repo="https://github.com/x/y.git",
                push=False,
            )
