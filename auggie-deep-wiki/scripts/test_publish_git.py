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

    def test_helper_present_for_url_returns_none(self, monkeypatch):
        # When git already has a credential helper for the host, the
        # script must not fall back to the env-var token: doing so would
        # bypass the helper and pin a stale value.
        monkeypatch.setenv("GITHUB_TOKEN", "stale-token")
        monkeypatch.setattr(
            pg, "_credential_helper_configured_for", lambda url: True
        )
        assert pg._resolve_token("https://github.com/x/y.git") is None

    def test_helper_absent_for_url_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "fresh-token")
        monkeypatch.setattr(
            pg, "_credential_helper_configured_for", lambda url: False
        )
        assert pg._resolve_token("https://github.com/x/y.git") == "fresh-token"


class TestCredentialHelperConfiguredFor:
    """``_credential_helper_configured_for`` shells out to ``git config
    --get-urlmatch credential.helper <url>`` and returns ``True`` only
    when git printed a non-empty helper line."""

    def test_returns_true_when_git_prints_helper(self, monkeypatch):
        def fake_run(cmd, **kw):
            assert cmd[:4] == ["git", "config", "--get-urlmatch", "credential.helper"]
            return subprocess.CompletedProcess(cmd, 0, "store\n", "")

        monkeypatch.setattr(pg.subprocess, "run", fake_run)
        assert pg._credential_helper_configured_for("https://github.com/x/y.git") is True

    def test_returns_false_when_git_exits_nonzero(self, monkeypatch):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr(pg.subprocess, "run", fake_run)
        assert pg._credential_helper_configured_for("https://github.com/x/y.git") is False

    def test_returns_false_on_empty_stdout(self, monkeypatch):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, "   \n", "")

        monkeypatch.setattr(pg.subprocess, "run", fake_run)
        assert pg._credential_helper_configured_for("https://github.com/x/y.git") is False

    def test_returns_false_when_git_unavailable(self, monkeypatch):
        def boom(cmd, **kw):
            raise OSError("git not found")

        monkeypatch.setattr(pg.subprocess, "run", boom)
        assert pg._credential_helper_configured_for("https://github.com/x/y.git") is False

    def test_returns_false_on_timeout(self, monkeypatch):
        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(pg.subprocess, "run", slow)
        assert pg._credential_helper_configured_for("https://github.com/x/y.git") is False

    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:org/repo.git",
            "ssh://git@github.com/org/repo.git",
            "file:///tmp/local-repo",
            "/tmp/local-repo",
        ],
    )
    def test_non_http_urls_short_circuit_to_false(self, monkeypatch, url):
        # ``--get-urlmatch`` is HTTP(S)-only and SSH transports use the
        # ssh-agent / key, not git credential helpers. The probe must not
        # even shell out for these URLs.
        called = {"n": 0}

        def fake_run(cmd, **kw):
            called["n"] += 1
            return subprocess.CompletedProcess(cmd, 0, "store\n", "")

        monkeypatch.setattr(pg.subprocess, "run", fake_run)
        assert pg._credential_helper_configured_for(url) is False
        assert called["n"] == 0, "git config must not be invoked for non-HTTP URLs"


class TestIsSshRepoUrl:
    """SSH detection feeds the dedicated AUTH_SSH path in ``resolve_auth``;
    a misclassification would either inject an unwanted ``Authorization``
    header into an SSH URL or label an HTTPS URL as SSH in logs."""

    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:org/repo.git",
            "ssh://git@github.com/org/repo.git",
            "git+ssh://git@github.com/org/repo.git",
            "user@host.example.com:path/to/repo.git",
            # scp-like without an explicit user.  Valid when the user's
            # ``~/.ssh/config`` sets ``User git`` for the host (or alias).
            "github.com:org/repo.git",
            "gh-work:org/repo.git",
            # Surrounding whitespace must not change the verdict.
            "  git@github.com:org/repo.git  ",
        ],
    )
    def test_ssh_forms_detected(self, url):
        assert pg._is_ssh_repo_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/org/repo.git",
            "http://example.com/repo.git",
            "file:///tmp/local-repo",
            "/tmp/local-repo",
            "./relative/repo",
            "../up/repo",
            "~/repo",
            # The git:// (anonymous git protocol) is HTTP-ish in spirit
            # and must not be classified as SSH.
            "git://github.com/org/repo.git",
            # Windows drive paths must not be misread as ``host:path``.
            "C:\\Users\\me\\repo",
            "C:/Users/me/repo",
            "D:\\repo",
            # email-like noise without a host:path colon must not be SSH.
            "noreply@github.com",
            "",
            "   ",
            # Path with a colon somewhere inside but no host prefix.
            "/var/git/has:colon/repo",
        ],
    )
    def test_non_ssh_forms_rejected(self, url):
        assert pg._is_ssh_repo_url(url) is False


class TestAuthModeConstants:
    """``publish()`` formats the ``Auth: <AUTH_*>`` log line with these
    literal tokens; downstream agents grep for them.  Renaming a
    constant here is a breaking change for log-parsing consumers, so
    pin the string values explicitly."""

    def test_constant_string_values_are_stable(self):
        assert pg.AUTH_HELPER == "git-credential-helper"
        assert pg.AUTH_HEADER == "http-authorization-header"
        assert pg.AUTH_SSH == "ssh-key"
        assert pg.AUTH_ANONYMOUS == "anonymous"


class TestResolveAuth:
    """Four explicit modes; the chosen mode drives both behaviour and
    diagnostic logging."""

    def test_helper_mode_when_helper_present(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_xxx")
        monkeypatch.setattr(
            pg, "_credential_helper_configured_for", lambda url: True
        )
        token, mode = pg.resolve_auth("https://github.com/x/y.git")
        assert token is None
        assert mode == pg.AUTH_HELPER

    def test_header_mode_when_no_helper_but_env_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        monkeypatch.setattr(
            pg, "_credential_helper_configured_for", lambda url: False
        )
        token, mode = pg.resolve_auth("https://github.com/x/y.git")
        assert token == "ghp_xxx"
        assert mode == pg.AUTH_HEADER

    def test_anonymous_mode_when_nothing_available(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setattr(
            pg, "_credential_helper_configured_for", lambda url: False
        )
        token, mode = pg.resolve_auth("https://github.com/x/y.git")
        assert token is None
        assert mode == pg.AUTH_ANONYMOUS

    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:org/repo.git",
            "ssh://git@github.com/org/repo.git",
        ],
    )
    def test_ssh_mode_takes_precedence_over_env_token(self, monkeypatch, url):
        # A token in the environment must NOT cause an SSH URL to be
        # classified as AUTH_HEADER - we have nowhere to inject the
        # header and the diagnostic message would be wrong.  The helper
        # probe must also be skipped (irrelevant for SSH transports).
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        called = {"helper_probe": 0}

        def fake_helper(url):
            called["helper_probe"] += 1
            return True  # would falsely trigger AUTH_HELPER if reached

        monkeypatch.setattr(pg, "_credential_helper_configured_for", fake_helper)
        token, mode = pg.resolve_auth(url)
        assert token is None
        assert mode == pg.AUTH_SSH
        assert called["helper_probe"] == 0, (
            "SSH branch must short-circuit before the helper probe"
        )


class TestClassifyGitError:
    """Maps known GitHub error fingerprints to actionable hints. Returns
    ``(None, None)`` for unrecognised output so the caller falls back to
    the raw stderr."""

    @pytest.mark.parametrize(
        "stderr,expected_category",
        [
            ("remote: invalid credentials\n", "auth-401"),
            ("fatal: could not read Username for 'https://github.com'", "auth-no-credential"),
            ("fatal: could not read Username: terminal prompts disabled", "auth-no-credential"),
            ("remote: Permission denied to user/x.\n", "auth-403"),
            ("remote: Repository not found.\nfatal: repository 'x' not found", "auth-404"),
            ("error: 403 Forbidden\n", "auth-403"),
            ("HTTP 401 returned\n", "auth-401"),
            # Generic git form when a credential is supplied but the
            # remote rejects it without echoing "invalid credentials"
            # or a bare HTTP status.
            (
                "fatal: Authentication failed for 'https://github.com/org/repo/'",
                "auth-401",
            ),
            # GitHub push-side wording when the credential authenticated
            # but lacks write scope.  Specific enough to deserve its own
            # fingerprint rather than relying on the bare "403" forms.
            (
                "remote: Write access to repository not granted.\nfatal: unable to access",
                "auth-403",
            ),
        ],
    )
    def test_known_signals_classified(self, stderr, expected_category):
        category, hint = pg._classify_git_error(stderr)
        assert category == expected_category
        assert hint and len(hint) > 20  # meaningful, not just a stub

    def test_unknown_signal_returns_none(self):
        assert pg._classify_git_error("network unreachable") == (None, None)

    def test_empty_stderr_returns_none(self):
        assert pg._classify_git_error("") == (None, None)

    @pytest.mark.parametrize(
        "stderr",
        [
            # Bare "403" / "401" must not match: only the disambiguated
            # forms ("status 403", "http 403", "error: 403", "code 403")
            # do.  These strings would have false-fired the older loose
            # classifier.
            "fatal: unable to access /tmp/path-with-403-in-it",
            "warning: redirected through 401-proxy.example.com",
            "remote: 4030 objects counted",
            "fatal: bad object refs/tags/v1.401.0",
        ],
    )
    def test_bare_status_codes_do_not_false_match(self, stderr):
        assert pg._classify_git_error(stderr) == (None, None)

    @pytest.mark.parametrize(
        "stderr,expected_category",
        [
            ("fatal: unable to access ...: The requested URL returned error: status 403", "auth-403"),
            ("HTTP 403 returned\n", "auth-403"),
            ("curl 22: error: 401 Unauthorized", "auth-401"),
            ("upload-pack: status 401\n", "auth-401"),
        ],
    )
    def test_disambiguated_status_codes_match(self, stderr, expected_category):
        category, hint = pg._classify_git_error(stderr)
        assert category == expected_category
        assert hint


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
        # ``skip_build_validation`` keeps these tests focused on the
        # arg/env wiring; ``validate_astro_build`` has its own coverage.
        result = pg.publish(
            output_dir=out,
            work_dir=tmp_path / "clone-env",
            push=False,
            skip_build_validation=True,
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
            skip_build_validation=True,
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

    # The npm-install failure tail can include ``.npmrc``-style auth
    # credentials when a transitive dep names a private registry.
    # ``_redact`` must mask each documented form before the tail lands
    # in a ``PublishError`` (and thus in CI logs).
    @pytest.mark.parametrize(
        "raw, secret",
        [
            ("//registry.npmjs.org/:_authToken=npm_abc123def", "npm_abc123def"),
            ("_authToken=ghp_supersecret", "ghp_supersecret"),
            ("//registry.example.com/:_auth=dXNlcjpwYXNz", "dXNlcjpwYXNz"),
            ("_password=base64encodedpw", "base64encodedpw"),
            ("Authorization: Bearer xoxb-slack-token-9999", "xoxb-slack-token-9999"),
        ],
    )
    def test_masks_npm_credentials(self, raw, secret):
        redacted = pg._redact(raw)
        assert secret not in redacted
        assert "***" in redacted

    def test_masks_inside_multiline_build_output(self):
        # Mirror what an npm-install failure tail actually looks like.
        log_block = (
            "npm error code E401\n"
            "npm error 401 Unauthorized - GET https://registry.example.com/foo\n"
            "npm error 401 In most cases you or one of your dependencies are\n"
            "//registry.example.com/:_authToken=ghs_topsecret_token_xyz\n"
            "//registry.example.com/:_password=YmFzZTY0cHc=\n"
            "Authorization: Bearer ghp_alsosecret\n"
        )
        redacted = pg._redact(log_block)
        for needle in (
            "ghs_topsecret_token_xyz", "YmFzZTY0cHc=", "ghp_alsosecret"
        ):
            assert needle not in redacted


# ---------------------------------------------------------------------------
# _format_proc_tail — independent stderr/stdout tailing for failure reports
# ---------------------------------------------------------------------------
class TestFormatProcTail:
    """``_format_proc_tail`` must surface the actual error even when one
    stream dominates the captured output.  Concatenating ``stderr`` and
    ``stdout`` before truncation -- which the previous helper did --
    let a chatty ``npm install`` / ``astro build`` ``stdout`` push the
    real error (almost always on ``stderr``) past the truncation
    window, leaving operators staring at progress noise.
    """

    def _proc(self, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["x"], returncode=1, stdout=stdout, stderr=stderr,
        )

    def test_long_stdout_does_not_hide_stderr(self):
        # ``npm install`` reliably prints hundreds of progress lines on
        # stdout while the actual error sits on stderr.  The helper
        # must show both.
        noisy_stdout = "\n".join(f"npm sill progress {i}" for i in range(500))
        real_error = "npm ERR! code E401\nnpm ERR! 401 Unauthorized"
        out = pg._format_proc_tail(self._proc(stdout=noisy_stdout, stderr=real_error))
        assert "npm ERR! code E401" in out
        assert "npm ERR! 401 Unauthorized" in out
        # Stream labels make the section boundaries explicit so the
        # operator can see which side the error came from.
        assert "stderr" in out
        assert "stdout" in out

    def test_each_stream_is_independently_truncated(self):
        # Both streams over the limit must each contribute up to the
        # tail limit -- not a shared budget.
        big_stdout = "\n".join(f"out-{i}" for i in range(pg.BUILD_OUTPUT_TAIL_LINES * 3))
        big_stderr = "\n".join(f"err-{i}" for i in range(pg.BUILD_OUTPUT_TAIL_LINES * 3))
        out = pg._format_proc_tail(self._proc(stdout=big_stdout, stderr=big_stderr))
        # Last lines of each stream are present.
        assert f"out-{pg.BUILD_OUTPUT_TAIL_LINES * 3 - 1}" in out
        assert f"err-{pg.BUILD_OUTPUT_TAIL_LINES * 3 - 1}" in out
        # Earliest lines were truncated from both.
        assert "out-0\n" not in out
        assert "err-0\n" not in out

    def test_credentials_redacted_in_both_streams(self):
        out = pg._format_proc_tail(self._proc(
            stdout="//registry.example.com/:_authToken=ghp_stdout_secret\n",
            stderr="Authorization: Bearer ghp_stderr_secret\n",
        ))
        assert "ghp_stdout_secret" not in out
        assert "ghp_stderr_secret" not in out

    def test_stdout_only_emits_only_stdout_section(self):
        out = pg._format_proc_tail(self._proc(stdout="just stdout"))
        assert "stdout" in out
        # No empty "stderr" section.
        assert "stderr" not in out

    def test_no_output_falls_back_to_placeholder(self):
        out = pg._format_proc_tail(self._proc())
        # An empty subprocess output should produce *something* the
        # operator can grep for instead of a silent blank line.
        assert out.strip() != ""


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

    def test_anonymous_fallback_succeeds_for_public_repo(self, tmp_path, monkeypatch):
        """When a token is supplied but yields 401/403/404, the script
        retries anonymously for the (common) case of a public host repo
        the token simply doesn't cover."""
        work_dir = tmp_path / "deep-wikis"
        attempts: list[dict[str, Any]] = []

        def fake_run(cmd, **kwargs):
            saw_header = any(
                isinstance(a, str) and a.startswith("http.extraHeader=")
                for a in cmd
            )
            attempts.append({"cmd": list(cmd), "with_header": saw_header})
            if saw_header:
                # First attempt: server says no.
                return subprocess.CompletedProcess(
                    cmd, 1, "", "remote: invalid credentials\nfatal: 401\n"
                )
            # Anonymous retry: succeeds. Materialize work_dir so
            # subsequent steps would find it.
            work_dir.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(pg, "_run", fake_run)
        pg.clone_host_repo("https://x/y.git", "main", work_dir, token="bad")

        assert len(attempts) == 2
        assert attempts[0]["with_header"] is True
        assert attempts[1]["with_header"] is False

    def test_no_anonymous_fallback_when_no_token(self, tmp_path, monkeypatch):
        """Without a token the failure is surfaced directly — no point
        in retrying the same anonymous call."""
        work_dir = tmp_path / "deep-wikis"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 1, "", "remote: invalid credentials\n"
            )

        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError, match="git clone failed"):
            pg.clone_host_repo("https://x/y.git", "main", work_dir, token=None)

    def test_non_auth_failure_is_not_retried(self, tmp_path, monkeypatch):
        """Network/branch-missing errors won't be cured by dropping
        credentials, so the script must not waste an anonymous retry."""
        work_dir = tmp_path / "deep-wikis"
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(
                cmd, 128, "", "fatal: unable to access 'https://x/y.git/': "
                "Could not resolve host: x\n",
            )

        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError, match="git clone failed"):
            pg.clone_host_repo("https://x/y.git", "main", work_dir, token="t")

        # Exactly one attempt — no anonymous retry on non-auth errors.
        assert len(calls) == 1

    def test_clone_log_redacts_url_userinfo(self, tmp_path, monkeypatch, caplog):
        """A caller-supplied URL with embedded ``user:token@`` userinfo
        must not leak into the log line that announces the clone."""
        work_dir = tmp_path / "deep-wikis"

        def fake_run(cmd, **kwargs):
            work_dir.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(pg, "_run", fake_run)
        caplog.set_level("INFO", logger=pg.log.name)
        pg.clone_host_repo(
            "https://user:ghp_supersecret@github.com/x/y.git",
            "main", work_dir, token=None,
        )
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "ghp_supersecret" not in joined
        assert "user:" not in joined  # userinfo prefix also stripped
        assert "***@github.com" in joined


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

        result = pg.publish(output_dir=out, push=False, skip_build_validation=True)
        assert result.pushed is False
        # The clone target lives under a temp dir that publish() must not
        # delete when push is disabled — operators rely on it for inspection.
        assert seen["work_dir"].exists()
        # Confirm we exercised the internal-tempdir path (not an explicit
        # work_dir) so this test still covers the --no-push preservation.
        assert mkdtemp_dir in seen["work_dir"].parents



# ---------------------------------------------------------------------------
# derive_slug — always produces a _SAFE_SLUG_RE-compatible value
# ---------------------------------------------------------------------------
class TestDeriveSlugFallback:
    """Guarantee :func:`derive_slug` never returns something that
    :func:`_sanitize_slug` would reject — otherwise ``--publish-git``
    would hard-fail on quirky upstream metadata.
    """

    @pytest.mark.parametrize(
        "metadata",
        [
            {"owner": "!!!", "name": "@@@"},          # all-symbol owner+name
            {"name": "***"},                          # all-symbol name
            {"owner": "", "name": ""},                # empty
            {"owner": "  ", "name": "\t\n"},          # whitespace-only
            {"name": "🔥🚀"},                          # non-ASCII only
        ],
    )
    def test_collapses_to_default_when_metadata_is_junk(self, metadata):
        slug = pg.derive_slug(None, metadata)
        assert slug == "wiki"
        # And the result is acceptable to the strict sanitizer.
        assert pg._sanitize_slug(slug) == "wiki"

    def test_partially_unicode_name_keeps_ascii_part(self):
        # Mixed ASCII + non-ASCII collapses cleanly to the ASCII subset
        # rather than falling back to ``"wiki"``.
        slug = pg.derive_slug(None, {"name": "café"})
        assert slug == "caf"
        assert pg._sanitize_slug(slug) == "caf"

    def test_truncates_overlong_owner_name(self):
        owner = "a" * 80
        name = "b" * 80
        slug = pg.derive_slug(None, {"owner": owner, "name": name})
        assert len(slug) <= 100
        # Sanity: still passes the strict guard.
        assert pg._sanitize_slug(slug) == slug

    def test_truncates_overlong_url(self):
        url = "https://github.com/" + "x" * 200 + "/" + "y" * 200
        slug = pg.derive_slug(url, None)
        assert 0 < len(slug) <= 100
        assert pg._sanitize_slug(slug) == slug

    def test_safe_slug_regex_match_after_truncation(self):
        # A slug that ends up exactly at the regex boundary (1 leading +
        # 99 trailing chars) is still valid.
        slug = pg._coerce_safe_slug("a" + "b" * 199)
        assert pg._SAFE_SLUG_RE.match(slug) is not None
        assert len(slug) == 100

    def test_strips_trailing_dash_after_truncation(self):
        # Without the rstrip the truncated slug could end with '-' which is
        # ugly even though the regex would still match it.
        raw = "abcd" + "!" * 100  # collapses to "abcd-...-" then truncates
        slug = pg._coerce_safe_slug(raw)
        assert not slug.endswith("-")
        assert pg._SAFE_SLUG_RE.match(slug) is not None

    def test_publish_does_not_raise_on_junk_metadata(
        self, tmp_path, monkeypatch
    ):
        # End-to-end: junk metadata with no --slug must not hard-fail.
        out = tmp_path / "out"
        out.mkdir()
        (out / "wiki.mdx").write_text("---\n# T\n---\n")
        (out / "repo_metadata.json").write_text(
            '{"owner": "!!!", "name": "***"}'
        )
        monkeypatch.setattr(
            pg, "clone_host_repo",
            lambda repo_url, branch, work_dir, *, token: (
                (work_dir / "src" / "content" / "wikis").mkdir(parents=True)
            ),
        )
        monkeypatch.setattr(pg, "commit_and_push", lambda *a, **kw: ("abc", False))

        result = pg.publish(
            output_dir=out,
            wiki_repo="https://github.com/x/y.git",
            work_dir=tmp_path / "clone",
            push=False,
            skip_build_validation=True,
        )
        assert result.slug == "wiki"


# ---------------------------------------------------------------------------
# commit_and_push — push-rejection retry only fires on non-fast-forward
# ---------------------------------------------------------------------------
class TestCommitAndPushScoping:
    """``commit_and_push`` must only stage the slug directory, never
    tooling artifacts that ``validate_astro_build`` may have left in
    the work dir (most notably ``package-lock.json`` from ``npm install``
    on the bare host-repo template).
    """

    @staticmethod
    def _git(*args: str, cwd: Path) -> None:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
            }
        )
        subprocess.run(
            ["git", *args], cwd=cwd, env=env, check=True, capture_output=True,
        )

    def _init_host_repo(self, tmp_path: Path) -> Path:
        """Init a real on-disk git repo with the host-repo layout."""
        work_dir = tmp_path / "host"
        work_dir.mkdir()
        self._git("init", "--initial-branch=main", cwd=work_dir)
        self._git("config", "commit.gpgsign", "false", cwd=work_dir)
        # seed with an empty initial commit so HEAD exists
        self._git("commit", "--allow-empty", "-m", "init", cwd=work_dir)
        (work_dir / "src" / "content" / "wikis").mkdir(parents=True)
        return work_dir

    def test_only_slug_dir_is_staged(self, tmp_path):
        """A stray ``package-lock.json`` from ``npm install`` must NOT
        end up in the publish commit.
        """
        work_dir = self._init_host_repo(tmp_path)
        # write the wiki entry inside the content collection
        slug_dir = work_dir / "src" / "content" / "wikis" / "abc"
        slug_dir.mkdir(parents=True)
        (slug_dir / "index.mdx").write_text("---\ntitle: x\n---\n")
        # simulate the validation side effects: a tooling artifact at
        # the repo root that git would otherwise pick up via ``-A``
        (work_dir / "package-lock.json").write_text('{"lockfileVersion": 3}')
        (work_dir / "package.json").write_text('{"name": "site"}')

        sha, pushed = pg.commit_and_push(
            work_dir,
            slug="abc", branch="main", push=False, token=None,
            author_name="t", author_email="t@e",
        )
        assert sha is not None
        assert pushed is False
        # Inspect what was actually committed
        proc = subprocess.run(
            ["git", "show", "--name-only", "--pretty=", "HEAD"],
            cwd=work_dir, check=True, capture_output=True, text=True,
        )
        committed = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
        assert committed == {"src/content/wikis/abc/index.mdx"}
        assert "package-lock.json" not in committed
        assert "package.json" not in committed
        # And the stray files remain untracked in the working tree
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=work_dir, check=True, capture_output=True, text=True,
        )
        # Both untracked artifacts should still appear as ``??`` entries
        statuses = [ln for ln in proc.stdout.splitlines() if ln]
        assert any("package-lock.json" in ln for ln in statuses)
        assert any("package.json" in ln for ln in statuses)

    def test_no_changes_in_slug_dir_skips_commit(self, tmp_path):
        """Even with stray tooling artifacts present, if the slug
        itself didn't change, no commit must be created.
        """
        work_dir = self._init_host_repo(tmp_path)
        # commit an existing wiki entry first
        slug_dir = work_dir / "src" / "content" / "wikis" / "abc"
        slug_dir.mkdir(parents=True)
        (slug_dir / "index.mdx").write_text("---\ntitle: x\n---\n")
        self._git("add", "src/content/wikis/abc", cwd=work_dir)
        self._git("commit", "-m", "seed", cwd=work_dir)
        head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=work_dir, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        # Now drop a stray lockfile (npm install side effect) but
        # leave the slug content untouched.
        (work_dir / "package-lock.json").write_text('{"lockfileVersion": 3}')

        sha, pushed = pg.commit_and_push(
            work_dir,
            slug="abc", branch="main", push=False, token=None,
            author_name="t", author_email="t@e",
        )
        assert sha is None
        assert pushed is False
        head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=work_dir, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        # No new commit was created.
        assert head_before == head_after


class TestPushRetryClassification:
    """The retry loop must only rebase-and-retry on the canonical
    non-fast-forward signals; protected-branch / hook rejections must
    surface immediately so the user can act on them.
    """

    def _setup_committed_repo(self, tmp_path):
        work_dir = tmp_path / "repo"
        work_dir.mkdir()
        return work_dir

    def _make_runner(self, push_stderr: str):
        """Return a fake ``_run`` that simulates a single failing push."""
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            verb = cmd[1] if cmd[:1] == ["git"] else (cmd[3] if len(cmd) > 3 else "")
            if verb == "diff":  # _has_staged_changes
                return subprocess.CompletedProcess(cmd, 1, "", "")
            if "push" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", push_stderr)
            return subprocess.CompletedProcess(cmd, 0, "deadbeef\n", "")

        return fake_run, calls

    def test_protected_branch_rejection_is_not_retried(self, tmp_path, monkeypatch):
        fake_run, calls = self._make_runner(
            "remote: error: GH006: Protected branch update failed for refs/heads/main.\n"
            "remote: error: At least one approving review is required.\n"
            "! [remote rejected] main -> main (protected branch hook declined)\n"
            "error: failed to push some refs to 'https://github.com/x/y.git'\n"
        )
        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError, match="git push failed"):
            pg.commit_and_push(
                self._setup_committed_repo(tmp_path),
                slug="abc", branch="main", push=True, token=None,
                author_name="x", author_email="x@y",
            )
        # Exactly one push attempt — no rebase loop, no further pushes.
        push_attempts = [c for c in calls if "push" in c]
        assert len(push_attempts) == 1
        assert not any("pull" in c for c in calls)

    def test_non_fast_forward_triggers_rebase_retry(self, tmp_path, monkeypatch):
        fake_run, calls = self._make_runner(
            "! [rejected] main -> main (non-fast-forward)\n"
            "error: failed to push some refs to 'https://github.com/x/y.git'\n"
            "hint: Updates were rejected because the tip of your current branch is behind\n"
            "hint: its remote counterpart. Integrate the remote changes (e.g.\n"
            "hint: 'git pull ...') before pushing again.\n"
        )
        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError, match="after .* retries"):
            pg.commit_and_push(
                self._setup_committed_repo(tmp_path),
                slug="abc", branch="main", push=True, token=None,
                author_name="x", author_email="x@y",
            )
        # Each failed push is followed by a pull --rebase before retrying.
        assert sum(1 for c in calls if "push" in c) == pg.PUSH_RETRIES
        assert sum(1 for c in calls if "pull" in c and "--rebase" in c) == pg.PUSH_RETRIES

    def test_invalid_credentials_raises_with_actionable_hint(
        self, tmp_path, monkeypatch
    ):
        """A 401 from the remote must surface the classifier hint so the
        user can tell whether to refresh the token or fix App scope —
        not just the raw 'remote: invalid credentials' line that started
        this whole investigation."""
        fake_run, calls = self._make_runner(
            "remote: invalid credentials\n"
            "fatal: Authentication failed for 'https://github.com/x/y.git/'\n"
        )
        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError, match="auth-401"):
            pg.commit_and_push(
                self._setup_committed_repo(tmp_path),
                slug="abc", branch="main", push=True, token=None,
                author_name="x", author_email="x@y",
            )
        # Single attempt — 401 is not retryable via rebase.
        assert sum(1 for c in calls if "push" in c) == 1
        assert not any("pull" in c for c in calls)

    def test_permission_denied_surfaces_403_hint(self, tmp_path, monkeypatch):
        fake_run, _ = self._make_runner(
            "remote: Permission to org/repo.git denied to user.\n"
            "fatal: unable to access 'https://github.com/x/y.git/': "
            "The requested URL returned error: 403\n"
        )
        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError, match="auth-403"):
            pg.commit_and_push(
                self._setup_committed_repo(tmp_path),
                slug="abc", branch="main", push=True, token=None,
                author_name="x", author_email="x@y",
            )


# ---------------------------------------------------------------------------
# validate_astro_build — tooling missing / install / build failure / success
# ---------------------------------------------------------------------------
class TestValidateAstroBuild:
    """Cover the three observable outcomes of ``validate_astro_build``:
    tooling missing (skip-with-summary), build failure (PublishError,
    no push), and success (no raise).
    """

    def _seed_host_repo(self, tmp_path: Path) -> Path:
        target = tmp_path / "deep-wikis"
        target.mkdir()
        (target / "package.json").write_text('{"name": "x"}')
        return target

    def test_missing_node_raises_build_tooling_missing(self, tmp_path, monkeypatch):
        # Both ``node`` and ``npm`` absent from PATH.
        monkeypatch.setattr(pg.shutil, "which", lambda _: None)
        target = self._seed_host_repo(tmp_path)
        with pytest.raises(pg.BuildToolingMissing) as excinfo:
            pg.validate_astro_build(target)
        msg = str(excinfo.value)
        assert "node" in msg and "npm" in msg

    def test_only_npm_missing_raises_build_tooling_missing(self, tmp_path, monkeypatch):
        # ``node`` present, ``npm`` missing — still a skip case.
        monkeypatch.setattr(
            pg.shutil, "which", lambda tool: "/usr/local/bin/node" if tool == "node" else None
        )
        with pytest.raises(pg.BuildToolingMissing) as excinfo:
            pg.validate_astro_build(self._seed_host_repo(tmp_path))
        msg = str(excinfo.value)
        # Only ``npm`` should be flagged as missing on the PATH report;
        # the ``node`` token may still appear in the install hint.
        path_line = msg.split("PATH:", 1)[1]
        assert "npm" in path_line
        assert "node" not in path_line.split("(", 1)[0]

    def test_missing_package_json_raises_publish_error(self, tmp_path, monkeypatch):
        # Tooling present but the host repo isn't an Astro project.
        monkeypatch.setattr(pg.shutil, "which", lambda _: "/usr/local/bin/" + _)
        target = tmp_path / "deep-wikis"
        target.mkdir()
        with pytest.raises(pg.PublishError, match="no package.json"):
            pg.validate_astro_build(target)

    def test_npm_install_runs_when_node_modules_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg.shutil, "which", lambda _: "/usr/local/bin/" + _)
        target = self._seed_host_repo(tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(pg, "_run", fake_run)
        pg.validate_astro_build(target)

        # Expect `npm install ...` followed by `npm run build ...`.
        assert calls[0][:2] == ["npm", "install"]
        assert ["npm", "run", "build"] == calls[1][:3]

    def test_npm_install_skipped_when_node_modules_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg.shutil, "which", lambda _: "/usr/local/bin/" + _)
        target = self._seed_host_repo(tmp_path)
        (target / "node_modules").mkdir()
        # Seed the sentinel that records the manifest hash from the last
        # successful install so freshness check returns ``True``.
        (target / "node_modules" / pg._PKG_HASH_SENTINEL).write_text(
            pg._pkg_manifest_hash(target)
        )
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(pg, "_run", fake_run)
        pg.validate_astro_build(target)

        # Only `npm run build` — no `npm install` call.
        assert all(c[:2] != ["npm", "install"] for c in calls)
        assert any(c[:3] == ["npm", "run", "build"] for c in calls)

    def test_pkg_manifest_drift_forces_reinstall(self, tmp_path, monkeypatch):
        """When ``package.json`` changes between runs, the cached
        ``node_modules`` must be discarded and reinstalled."""
        monkeypatch.setattr(pg.shutil, "which", lambda _: "/usr/local/bin/" + _)
        target = self._seed_host_repo(tmp_path)
        node_modules = target / "node_modules"
        node_modules.mkdir()
        # Pretend a previous run installed against a different manifest.
        (node_modules / pg._PKG_HASH_SENTINEL).write_text("stale-hash")
        # Drop a fake nested file so we can verify the rmtree happened.
        (node_modules / "leftover.txt").write_text("from previous run")

        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            # Simulate npm install actually creating node_modules anew.
            if cmd[:2] == ["npm", "install"]:
                node_modules.mkdir(exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(pg, "_run", fake_run)
        pg.validate_astro_build(target)

        # npm install ran (drift forced reinstall) before the build.
        assert calls[0][:2] == ["npm", "install"]
        # The leftover from the prior run was removed.
        assert not (node_modules / "leftover.txt").exists()
        # New sentinel matches the current manifest.
        assert (node_modules / pg._PKG_HASH_SENTINEL).read_text() == pg._pkg_manifest_hash(target)

    def test_npm_install_failure_becomes_publish_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg.shutil, "which", lambda _: "/usr/local/bin/" + _)
        target = self._seed_host_repo(tmp_path)

        def fake_run(cmd, **kw):
            if cmd[:2] == ["npm", "install"]:
                # Simulate a partial install: node_modules exists but
                # nothing inside it is usable.
                (target / "node_modules").mkdir(exist_ok=True)
                (target / "node_modules" / "half-installed.txt").write_text("oops")
                return subprocess.CompletedProcess(cmd, 1, "", "EACCES denied")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError, match="npm install failed"):
            pg.validate_astro_build(target)
        # Failure cleanup: the partial node_modules tree is removed so
        # the next run starts clean instead of silently reusing it.
        assert not (target / "node_modules").exists()

    def test_build_failure_becomes_publish_error_with_tail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg.shutil, "which", lambda _: "/usr/local/bin/" + _)
        target = self._seed_host_repo(tmp_path)
        (target / "node_modules").mkdir()
        long_err = "\n".join(f"line-{i}" for i in range(200))

        def fake_run(cmd, **kw):
            if cmd[:3] == ["npm", "run", "build"]:
                return subprocess.CompletedProcess(cmd, 1, long_err, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(pg, "_run", fake_run)
        with pytest.raises(pg.PublishError) as excinfo:
            pg.validate_astro_build(target)
        msg = str(excinfo.value)
        assert "astro build failed" in msg
        # Last lines should be present, earliest should be elided.
        assert "line-199" in msg
        assert "line-0" not in msg



# ---------------------------------------------------------------------------
# publish() integration with build validation
# ---------------------------------------------------------------------------
class TestPublishBuildValidationIntegration:
    """Verify ``publish()`` wires up ``validate_astro_build`` correctly:
    skip-with-summary on missing tooling, hard-fail on build error, and
    bypass when ``skip_build_validation=True``.
    """

    def _make_output(self, tmp_path: Path) -> Path:
        out = tmp_path / "out"
        out.mkdir()
        (out / "wiki.mdx").write_text("---\n# T\n---\n")
        (out / "repo_metadata.json").write_text(
            '{"owner": "pallets", "name": "click"}'
        )
        return out

    def _seed_clone(self, monkeypatch):
        """Stub ``clone_host_repo`` to set up the minimum host-repo layout
        ``write_entry`` and ``validate_astro_build`` need.
        """
        def fake_clone(repo_url, branch, work_dir, *, token):
            (work_dir / "src" / "content" / "wikis").mkdir(parents=True)
            (work_dir / "package.json").write_text('{"name": "x"}')
        monkeypatch.setattr(pg, "clone_host_repo", fake_clone)

    def test_skip_flag_bypasses_validation_and_pushes(self, tmp_path, monkeypatch):
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        self._seed_clone(monkeypatch)

        validate_called = {"count": 0}

        def fake_validate(*a, **kw):
            validate_called["count"] += 1

        monkeypatch.setattr(pg, "validate_astro_build", fake_validate)
        monkeypatch.setattr(pg, "commit_and_push", lambda *a, **kw: ("abc", True))

        result = pg.publish(
            output_dir=out,
            work_dir=tmp_path / "clone",
            skip_build_validation=True,
        )
        assert validate_called["count"] == 0
        assert result.pushed is True
        assert result.validation_skipped is True
        assert "bypassed" in (result.validation_skipped_reason or "")
        # ``--skip-build-validation`` is operator intent, NOT missing
        # tooling: the CLI must not flip to exit 3 in this case.
        assert result.tooling_missing is False

    def test_skip_flag_with_idempotent_no_op_does_not_signal_tooling_missing(
        self, tmp_path, monkeypatch,
    ):
        """Regression for the false-positive flagged in PR #3 review:
        ``--skip-build-validation`` plus an idempotent run that has
        nothing to push results in
        ``validation_skipped=True`` + ``pushed=False`` + push requested,
        which used to satisfy the heuristic exit-3 check.  The
        dedicated ``tooling_missing`` flag must stay False so callers
        treat this as a successful no-op.
        """
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        self._seed_clone(monkeypatch)

        monkeypatch.setattr(pg, "validate_astro_build", lambda *a, **kw: None)
        # ``commit_and_push`` returns ``pushed=False`` to model "index
        # was empty, nothing to commit" -- the idempotent re-run path.
        monkeypatch.setattr(pg, "commit_and_push", lambda *a, **kw: (None, False))

        result = pg.publish(
            output_dir=out,
            work_dir=tmp_path / "clone",
            skip_build_validation=True,
        )
        assert result.validation_skipped is True
        assert result.pushed is False
        assert result.tooling_missing is False

    def test_missing_tooling_skips_push_and_preserves_workdir(
        self, tmp_path, monkeypatch
    ):
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        self._seed_clone(monkeypatch)

        def raise_missing(*a, **kw):
            raise pg.BuildToolingMissing("required build tooling not found on PATH: node, npm")

        commit_called = {"count": 0}

        def fake_commit(*a, **kw):
            commit_called["count"] += 1
            return ("abc", True)

        monkeypatch.setattr(pg, "validate_astro_build", raise_missing)
        monkeypatch.setattr(pg, "commit_and_push", fake_commit)

        clone_dir = tmp_path / "clone"
        result = pg.publish(output_dir=out, work_dir=clone_dir)

        # No commit, no push, but the entry was written and the dir lives.
        assert commit_called["count"] == 0
        assert result.pushed is False
        assert result.validation_skipped is True
        assert "node" in (result.validation_skipped_reason or "")
        assert result.tooling_missing is True
        assert clone_dir.is_dir()

    def test_build_failure_propagates_and_skips_push(self, tmp_path, monkeypatch):
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        self._seed_clone(monkeypatch)

        def raise_build_error(*a, **kw):
            raise pg.PublishError("astro build failed: bad indentation")

        commit_called = {"count": 0}

        def fake_commit(*a, **kw):
            commit_called["count"] += 1
            return ("abc", True)

        monkeypatch.setattr(pg, "validate_astro_build", raise_build_error)
        monkeypatch.setattr(pg, "commit_and_push", fake_commit)

        with pytest.raises(pg.PublishError, match="astro build failed"):
            pg.publish(output_dir=out, work_dir=tmp_path / "clone")
        assert commit_called["count"] == 0

    def test_build_failure_preserves_default_temp_clone(
        self, tmp_path, monkeypatch,
    ):
        """SKILL.md promises the clone is preserved on build failure so
        the operator can ``cd`` in and reproduce locally.  That has to
        hold for the *default* (no ``--work-dir``) ephemeral-temp path
        too, not just for the explicit ``--work-dir`` case -- otherwise
        the docs lie.  Capture the temp dir ``publish()`` chose via
        ``tempfile.mkdtemp`` and assert it's still on disk after the
        ``PublishError`` propagates out.
        """
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        self._seed_clone(monkeypatch)

        # Capture every directory ``mkdtemp`` hands out so we can check
        # it survived after the failure.  We don't override the helper;
        # we just record what it returns.
        created: list[str] = []
        real_mkdtemp = pg.tempfile.mkdtemp

        def recording_mkdtemp(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            created.append(d)
            return d

        monkeypatch.setattr(pg.tempfile, "mkdtemp", recording_mkdtemp)
        monkeypatch.setattr(
            pg, "validate_astro_build",
            lambda *a, **kw: (_ for _ in ()).throw(
                pg.PublishError("astro build failed: bad mdx")
            ),
        )
        monkeypatch.setattr(pg, "commit_and_push", lambda *a, **kw: ("x", True))

        with pytest.raises(pg.PublishError, match="astro build failed"):
            pg.publish(output_dir=out)  # no work_dir => default temp path

        # Exactly one temp dir was allocated and it is still on disk.
        assert len(created) == 1
        assert Path(created[0]).is_dir()
        # And the cloned host repo inside it survived too (this is what
        # the operator actually wants to inspect).
        assert (Path(created[0]) / "deep-wikis").is_dir()

    def test_validation_success_proceeds_to_push(self, tmp_path, monkeypatch):
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        self._seed_clone(monkeypatch)

        validate_called = {"count": 0}
        commit_called = {"count": 0}

        def fake_validate(*a, **kw):
            validate_called["count"] += 1

        def fake_commit(*a, **kw):
            commit_called["count"] += 1
            return ("deadbeef", True)

        monkeypatch.setattr(pg, "validate_astro_build", fake_validate)
        monkeypatch.setattr(pg, "commit_and_push", fake_commit)

        result = pg.publish(output_dir=out, work_dir=tmp_path / "clone")
        assert validate_called["count"] == 1
        assert commit_called["count"] == 1
        assert result.pushed is True
        assert result.validation_skipped is False
        assert result.validation_skipped_reason is None

    def test_no_push_dry_run_skips_validation(self, tmp_path, monkeypatch):
        """``--no-push`` is a dry run; paying the install/build cost is
        wasteful and inconsistent with the operator's intent.  The
        publisher should commit locally only and report the skip.
        """
        out = self._make_output(tmp_path)
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        self._seed_clone(monkeypatch)

        validate_called = {"count": 0}

        def fake_validate(*a, **kw):
            validate_called["count"] += 1

        monkeypatch.setattr(pg, "validate_astro_build", fake_validate)
        monkeypatch.setattr(pg, "commit_and_push", lambda *a, **kw: ("abc", False))

        result = pg.publish(
            output_dir=out, work_dir=tmp_path / "clone", push=False,
        )
        # Validation never runs; the dry-run commit still happens.
        assert validate_called["count"] == 0
        assert result.pushed is False
        assert result.validation_skipped is True
        assert "dry run" in (result.validation_skipped_reason or "").lower()


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------
class TestCliExitCodes:
    """The ``main()`` exit code is part of the CLI contract; CI uses
    it to distinguish failure modes from intentional skip states."""

    def _setup(self, tmp_path, monkeypatch):
        out = tmp_path / "out"
        out.mkdir()
        (out / "wiki.mdx").write_text("---\n# T\n---\n")
        (out / "repo_metadata.json").write_text('{"owner": "o", "name": "n"}')
        monkeypatch.setenv("DEEP_WIKIS_GIT_REPO", "https://github.com/x/y.git")
        return out

    def test_no_push_returns_zero_even_with_validation_skipped(
        self, tmp_path, monkeypatch
    ):
        """``--no-push`` flips ``validation_skipped`` to True (dry-run
        skip), but that is a successful run from the user's POV → 0.
        """
        out = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(
            pg, "publish",
            lambda **_: pg.PublishResult(
                repo_url="https://github.com/x/y.git", branch="main",
                slug="o-n", entry_path=Path("src/content/wikis/o-n/index.mdx"),
                commit_sha="abc", pushed=False,
                validation_skipped=True,
                validation_skipped_reason="dry run (--no-push); validation skipped",
            ),
        )
        rc = pg.main([
            "--output-dir", str(out), "--no-push",
        ])
        assert rc == 0

    def test_tooling_missing_returns_three(self, tmp_path, monkeypatch, caplog):
        """When push was *requested* but tooling was missing the CLI
        returns 3 so callers can distinguish "fix your env" from
        either success (0) or a hard failure (1).  The recovery hint
        must include all four manual git steps (build + add + commit
        + push), not just ``git push`` -- the publisher bails *before*
        ``commit_and_push`` in this branch, so the entry is on the
        working tree only.
        """
        out = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(
            pg, "publish",
            lambda **_: pg.PublishResult(
                repo_url="https://github.com/x/y.git", branch="main",
                slug="o-n", entry_path=Path("src/content/wikis/o-n/index.mdx"),
                commit_sha=None, pushed=False,
                validation_skipped=True,
                validation_skipped_reason="required build tooling not found on PATH: node, npm",
                tooling_missing=True,
            ),
        )
        with caplog.at_level("ERROR", logger="auggie-deep-wiki.publish-git"):
            rc = pg.main(["--output-dir", str(out)])
        assert rc == 3
        recovery = "\n".join(r.getMessage() for r in caplog.records)
        # All four steps must be in the hint, in order, scoped to the
        # slug directory so build artifacts don't sneak into the commit.
        assert "npm install" in recovery
        assert "npm run build" in recovery
        assert "git add -- src/content/wikis/o-n" in recovery
        assert "git commit" in recovery
        assert "git push origin main" in recovery
        # And in the right order.
        assert recovery.index("npm install") < recovery.index("git add")
        assert recovery.index("git add") < recovery.index("git commit")
        assert recovery.index("git commit") < recovery.index("git push")

    def test_skip_validation_idempotent_returns_zero(
        self, tmp_path, monkeypatch,
    ):
        """Regression for the false-positive in PR #3 review:
        ``--skip-build-validation`` + idempotent run (no diff to push)
        flips ``validation_skipped`` and ``pushed=False`` even though
        push *was* requested.  The previous heuristic returned 3 here;
        with the dedicated ``tooling_missing`` flag it must return 0.
        """
        out = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(
            pg, "publish",
            lambda **_: pg.PublishResult(
                repo_url="https://github.com/x/y.git", branch="main",
                slug="o-n", entry_path=Path("src/content/wikis/o-n/index.mdx"),
                commit_sha=None, pushed=False,
                validation_skipped=True,
                validation_skipped_reason="explicitly bypassed (--skip-build-validation)",
                tooling_missing=False,
            ),
        )
        rc = pg.main(["--output-dir", str(out), "--skip-build-validation"])
        assert rc == 0

    def test_publish_error_returns_one(self, tmp_path, monkeypatch):
        out = self._setup(tmp_path, monkeypatch)

        def boom(**_):
            raise pg.PublishError("clone failed")

        monkeypatch.setattr(pg, "publish", boom)
        rc = pg.main(["--output-dir", str(out)])
        assert rc == 1
