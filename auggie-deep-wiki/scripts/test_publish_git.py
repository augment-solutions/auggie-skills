"""Unit tests for ``publish_git`` pure helpers.

Run with::

    python3 -m pytest scripts/test_publish_git.py -v

Covers:
- ``derive_slug``           — metadata-first, URL fallback, default.
- ``_sanitize_slug``        — directory-traversal guard.
- ``_strip_existing_frontmatter`` — assembler bookend handling.
- ``build_entry_mdx``       — Astro frontmatter assembly with control-char escaping.
- ``_yaml_scalar``          — quoting, control chars, lists, bools.
- ``_git_base`` / ``_resolve_token`` — auth-header injection.
- ``_run`` / ``_redact``    — token masking + timeout translation.
- ``write_entry``           — atomic content-collection entry replacement.
- ``publish``               — end-to-end with mocked git/clone (no network).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

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

        # Use an explicit work_dir under tmp_path so pytest cleans up after
        # the test; otherwise publish() with ``push=False`` would leave a
        # ``deep-wikis-clone-*`` dir in the system temp directory.
        result = pg.publish(
            output_dir=out, work_dir=tmp_path / "clone-env", push=False
        )
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
            work_dir=tmp_path / "clone-explicit",
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



# ---------------------------------------------------------------------------
# slug sanitization (directory-traversal guard)
# ---------------------------------------------------------------------------
class TestSanitizeSlug:
    @pytest.mark.parametrize("slug", ["pallets-click", "abc", "a", "x_y-z", "repo123"])
    def test_accepts_valid(self, slug):
        assert pg._sanitize_slug(slug) == slug

    @pytest.mark.parametrize(
        "slug",
        [
            "",
            " ",
            "..",
            "../escape",
            "../../etc/passwd",
            "/abs/path",
            "with/slash",
            "back\\slash",
            "-leading-dash",
            "_leading-underscore",
            "UPPER",
            "café",
            "name with space",
            "x" * 101,
        ],
    )
    def test_rejects_invalid(self, slug):
        with pytest.raises(pg.PublishError, match="Invalid slug"):
            pg._sanitize_slug(slug)

    def test_write_entry_rejects_traversal(self, tmp_path):
        with pytest.raises(pg.PublishError, match="Invalid slug"):
            pg.write_entry(
                tmp_path,
                "../escape",
                wiki_mdx="# x",
                metadata={"name": "x"},
                structure=None,
            )
        # Nothing written above the content collection root.
        assert not (tmp_path.parent / "escape").exists()


# ---------------------------------------------------------------------------
# _redact + _run token masking and timeout translation
# ---------------------------------------------------------------------------
class TestRedact:
    def test_masks_authorization_header_in_string(self):
        out = pg._redact(
            "git -c http.extraHeader=Authorization: Bearer ghp_secret123 clone url"
        )
        assert "ghp_secret123" not in out
        assert "Bearer ***" in out

    def test_masks_userinfo_in_url(self):
        assert pg._redact("https://user:pass@github.com/x") == "https://***@github.com/x"

    def test_redact_cmd_handles_token_token(self):
        cmd = pg._git_base("ghp_topsecret") + ["clone", "https://github.com/x.git"]
        rendered = pg._redact_cmd(cmd)
        assert "ghp_topsecret" not in rendered
        assert "Bearer ***" in rendered


class TestRunHelper:
    def test_timeout_becomes_publish_error(self):
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        with mock.patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(pg.PublishError, match="timed out after 5s"):
                pg._run(["git", "clone", "url"], timeout=5)

    def test_publish_error_redacts_command_token(self):
        # Stderr that mimics a real git failure (no raw token) — the review
        # flagged the *command* being embedded verbatim, which is what we
        # need to verify here.
        completed = subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="", stderr="fatal: not allowed"
        )
        with mock.patch("subprocess.run", return_value=completed):
            cmd = pg._git_base("ghp_secret123") + ["push"]
            with pytest.raises(pg.PublishError) as excinfo:
                pg._run(cmd, capture=True)
        msg = str(excinfo.value)
        assert "ghp_secret123" not in msg
        assert "Bearer ***" in msg

    def test_oserror_becomes_publish_error(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            with pytest.raises(pg.PublishError, match="not runnable"):
                pg._run(["git", "version"])


# ---------------------------------------------------------------------------
# clone_host_repo — reusable existing work_dir
# ---------------------------------------------------------------------------
class TestCloneHostRepo:
    def test_existing_clone_is_refreshed(self, tmp_path, monkeypatch):
        work_dir = tmp_path / "deep-wikis"
        (work_dir / ".git").mkdir(parents=True)
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(pg, "_run", fake_run)
        pg.clone_host_repo("https://x/y.git", "main", work_dir, token=None)

        verbs = [c[1] for c in calls]
        assert "clone" not in verbs
        assert "fetch" in verbs
        assert "reset" in verbs
        assert "clean" in verbs

    def test_non_git_dir_rejected(self, tmp_path):
        work_dir = tmp_path / "stuff"
        work_dir.mkdir()
        (work_dir / "unrelated.txt").write_text("hi")
        with pytest.raises(pg.PublishError, match="not a git repository"):
            pg.clone_host_repo("https://x/y.git", "main", work_dir, token=None)

    def test_file_target_rejected(self, tmp_path):
        # A regular file at ``work_dir`` would make ``iterdir()`` raise
        # ``NotADirectoryError`` — verify we surface a PublishError instead.
        work_dir = tmp_path / "not-a-dir"
        work_dir.write_text("oops")
        with pytest.raises(pg.PublishError, match="not a directory"):
            pg.clone_host_repo("https://x/y.git", "main", work_dir, token=None)


# ---------------------------------------------------------------------------
# write_entry — symlink escape guard
# ---------------------------------------------------------------------------
class TestWriteEntrySymlinkGuard:
    def test_symlinked_content_dir_rejected(self, tmp_path):
        # Simulate a malicious host repo where ``src/content/wikis`` is a
        # symlink to a directory outside the clone root.  Without the
        # ``relative_to(work_root)`` check the subsequent rmtree/write would
        # operate on the symlink target.
        work_dir = tmp_path / "clone"
        (work_dir / "src" / "content").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, work_dir / "src" / "content" / "wikis")

        with pytest.raises(pg.PublishError, match="outside clone root"):
            pg.write_entry(
                work_dir,
                "pallets-click",
                wiki_mdx="# X",
                metadata={"name": "click"},
                structure=None,
            )
        # And nothing was written into the symlink target.
        assert list(outside.iterdir()) == []

    def test_symlinked_slug_dir_rejected(self, tmp_path):
        # ``src/content/wikis`` itself is a real directory but the slug
        # already exists as a symlink pointing outside the clone — replacing
        # it would still escape ``work_dir``.
        work_dir = tmp_path / "clone"
        wikis = work_dir / "src" / "content" / "wikis"
        wikis.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, wikis / "pallets-click")

        with pytest.raises(pg.PublishError, match="outside clone root"):
            pg.write_entry(
                work_dir,
                "pallets-click",
                wiki_mdx="# X",
                metadata={"name": "click"},
                structure=None,
            )
        assert list(outside.iterdir()) == []


# ---------------------------------------------------------------------------
# publish() — --no-push preserves the temp work dir
# ---------------------------------------------------------------------------
class TestPublishNoPushPreservesWorkDir:
    def test_temp_dir_kept_when_push_disabled(self, tmp_path, monkeypatch):
        out = tmp_path / "out"
        out.mkdir()
        (out / "wiki.mdx").write_text("---\n# T\n---\n")
        (out / "repo_metadata.json").write_text(
            '{"owner": "pallets", "name": "click"}'
        )
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")

        # Redirect publish()'s internal ``tempfile.mkdtemp`` under ``tmp_path``
        # so pytest cleans up the preserved temp dir after the test. Without
        # this, ``push=False`` (which intentionally skips cleanup) would leak
        # ``deep-wikis-clone-*`` into the system temp directory.
        mkdtemp_dir = tmp_path / "mkdtemp-root"
        mkdtemp_dir.mkdir()

        def fake_mkdtemp(prefix: str = "tmp", **_kw: Any) -> str:
            d = mkdtemp_dir / f"{prefix}fake"
            d.mkdir()
            return str(d)

        monkeypatch.setattr(pg.tempfile, "mkdtemp", fake_mkdtemp)

        seen: dict[str, Path] = {}

        def fake_clone(repo_url, branch, work_dir, *, token):
            (work_dir / "src" / "content" / "wikis").mkdir(parents=True)
            seen["work_dir"] = work_dir

        monkeypatch.setattr(pg, "clone_host_repo", fake_clone)
        monkeypatch.setattr(pg, "commit_and_push", lambda *a, **kw: ("abc", False))

        result = pg.publish(output_dir=out, push=False)
        assert result.pushed is False
        # The clone target lives under a temp dir that publish() must not
        # delete when push is disabled — operators rely on it for inspection.
        assert seen["work_dir"].exists()
        # Confirm we exercised the internal-tempdir path (not an explicit
        # work_dir) so this test still covers the --no-push preservation.
        assert mkdtemp_dir in seen["work_dir"].parents
