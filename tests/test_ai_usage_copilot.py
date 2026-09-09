"""Tests for bin/ai-usage-copilot.

Covers spec.md's acceptance criteria 8, 11-14: the pinned gh invocation
(argv/env), redirect and TLS-certificate failures exercised through the real
opener (not a stubbed-out one), and that no synthetic token/marker ever
reaches stdout, stderr, or a subprocess argument/environment.
"""
import http.server
import importlib.machinery
import importlib.util
import json
import subprocess
import ssl
import sys
import threading
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "bin" / "ai-usage-copilot"

SYNTHETIC_TOKEN = "ghu_SYNTHETIC0123456789TOKENMARKER"
AUTH_MARKER = "token " + SYNTHETIC_TOKEN

SAMPLE_PAYLOAD = {
    "copilot_plan": "enterprise",
    "quota_reset_date": "2026-10-01",
    "quota_snapshots": {
        "chat": {"unlimited": True, "remaining": 0, "entitlement": 0, "credits_used": 0},
        "completions": {"unlimited": True, "remaining": 0, "entitlement": 0, "credits_used": 0},
        "premium_interactions": {"unlimited": True, "remaining": 0, "entitlement": 0, "credits_used": 11807},
    },
}


def load_module():
    # The script has no .py suffix, so importlib can't infer a loader from
    # the extension -- give it the source-file loader explicitly.
    loader = importlib.machinery.SourceFileLoader("ai_usage_copilot", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_file_location("ai_usage_copilot", SCRIPT_PATH, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod():
    return load_module()


# ---------------------------------------------------------------- get_token

def test_get_token_pins_hostname_and_user_and_shell_false(mod, monkeypatch):
    captured = {}

    def fake_run(command, capture_output, shell, env, text):
        captured["command"] = command
        captured["shell"] = shell
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0, stdout=SYNTHETIC_TOKEN + "\n", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    token = mod.get_token()

    assert token == SYNTHETIC_TOKEN
    assert captured["command"] == ["gh", "auth", "token", "--hostname", "github.com", "--user", "bobbyw_jsi"]
    assert captured["shell"] is False


def test_get_token_strips_override_vars_without_mutating_parent_env(mod, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "conflicting-env-token")
    monkeypatch.setenv("GITHUB_TOKEN", "conflicting-env-token")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "conflicting-env-token")
    monkeypatch.setenv("GITHUB_ENTERPRISE_TOKEN", "conflicting-env-token")

    captured = {}

    def fake_run(command, capture_output, shell, env, text):
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0, stdout=SYNTHETIC_TOKEN, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.get_token()

    for var in mod.TOKEN_OVERRIDE_VARS:
        assert var not in captured["env"]
    # The real environment (what monkeypatch will restore) is untouched --
    # get_token only ever mutates its own copy.
    import os
    for var in mod.TOKEN_OVERRIDE_VARS:
        assert os.environ.get(var) == "conflicting-env-token"


def test_get_token_missing_gh_binary_fails_without_env_fallback(mod, monkeypatch, capsys):
    def fake_run(*a, **k):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        mod.get_token()
    assert exc.value.code == 1
    out = capsys.readouterr()
    assert "unavailable" in out.err.lower()
    assert SYNTHETIC_TOKEN not in out.err
    assert out.out == ""


def test_get_token_nonzero_exit_fails_without_stderr_leak(mod, monkeypatch, capsys):
    def fake_run(command, capture_output, shell, env, text):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="error: " + SYNTHETIC_TOKEN)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        mod.get_token()
    assert exc.value.code == 1
    out = capsys.readouterr()
    assert SYNTHETIC_TOKEN not in out.err
    assert SYNTHETIC_TOKEN not in out.out


def test_get_token_empty_stdout_fails(mod, monkeypatch):
    def fake_run(command, capture_output, shell, env, text):
        return subprocess.CompletedProcess(command, 0, stdout="  \n", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        mod.get_token()
    assert exc.value.code == 1


# ------------------------------------------------------------- build_record

def test_build_record_shapes_the_three_categories(mod):
    record = mod.build_record(SAMPLE_PAYLOAD)
    assert record["plan"] == "enterprise"
    assert record["quotaResetDate"] == "2026-10-01"
    assert set(record["categories"]) == {"chat", "completions", "premium_interactions"}
    assert record["categories"]["premium_interactions"]["creditsUsed"] == 11807
    assert record["categories"]["chat"]["unlimited"] is True


def test_build_record_missing_field_fails_without_leaking_payload(mod, capsys):
    broken = {"copilot_plan": "enterprise"}  # no quota_snapshots
    with pytest.raises(SystemExit) as exc:
        mod.build_record(broken)
    assert exc.value.code == 1
    out = capsys.readouterr()
    assert "enterprise" not in out.err


# ------------------------------------------------------- fetch_quota (mocked)
# These failure modes (malformed JSON, generic HTTP error) aren't the
# redirect/TLS cases spec.md's acceptance criterion 11 requires a real
# transport for, so a stubbed opener is sufficient here.

def test_fetch_quota_malformed_json_fails(mod, monkeypatch):
    class FakeResponse:
        def read(self):
            return b"not json"

        def close(self):
            pass

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()

    monkeypatch.setattr(mod.urllib.request, "build_opener", lambda *a: FakeOpener())
    with pytest.raises(SystemExit) as exc:
        mod.fetch_quota(SYNTHETIC_TOKEN)
    assert exc.value.code == 1


def test_fetch_quota_http_error_fails(mod, monkeypatch):
    class FakeOpener:
        def open(self, request, timeout):
            raise mod.urllib.error.HTTPError(mod.API_URL, 404, "Not Found", {}, None)

    monkeypatch.setattr(mod.urllib.request, "build_opener", lambda *a: FakeOpener())
    with pytest.raises(SystemExit) as exc:
        mod.fetch_quota(SYNTHETIC_TOKEN)
    assert exc.value.code == 1


def test_fetch_quota_sends_authorization_only_in_headers(mod, monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps(SAMPLE_PAYLOAD).encode()

        def close(self):
            pass

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            return FakeResponse()

    monkeypatch.setattr(mod.urllib.request, "build_opener", lambda *a: FakeOpener())
    mod.fetch_quota(SYNTHETIC_TOKEN)

    request = captured["request"]
    assert request.full_url == mod.API_URL
    assert request.get_header("Authorization") == AUTH_MARKER
    assert SYNTHETIC_TOKEN not in request.full_url


# ------------------------------------------------------ real-transport tests
# Acceptance criterion 11 requires these two cases to exercise the actual
# production opener (build_opener(NoRedirectHandler) + default handlers),
# not a stub -- so they run a real local server over a real socket.

class _CountingHandler(http.server.BaseHTTPRequestHandler):
    """Serves a fixed redirect for its first path, and records whether the
    redirect target was ever hit -- proving NoRedirectHandler killed the chain."""

    redirect_code = 302
    location = "/redirected"  # overridden per-test: relative, or an absolute same-/cross-host URL
    hits = []

    def do_GET(self):
        _CountingHandler.hits.append(self.path)
        if self.path == "/first":
            self.send_response(_CountingHandler.redirect_code)
            self.send_header("Location", _CountingHandler.location)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(SAMPLE_PAYLOAD).encode())

    def log_message(self, *args):
        pass


def _start_server(handler_cls=_CountingHandler):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture()
def redirect_server():
    _CountingHandler.hits = []
    _CountingHandler.location = "/redirected"
    server, thread = _start_server()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirects_are_never_followed_by_the_real_opener(mod, redirect_server, code):
    _CountingHandler.redirect_code = code
    port = redirect_server.server_address[1]
    url = f"http://127.0.0.1:{port}/first"

    with pytest.raises(SystemExit) as exc:
        mod.fetch_quota(SYNTHETIC_TOKEN, url=url)
    assert exc.value.code == 1
    # The server saw exactly the one request -- the redirect target was
    # never fetched.
    assert _CountingHandler.hits == ["/first"]


def test_redirect_to_absolute_same_host_url_is_never_followed(mod, redirect_server):
    _CountingHandler.redirect_code = 302
    port = redirect_server.server_address[1]
    _CountingHandler.location = f"http://127.0.0.1:{port}/redirected"
    url = f"http://127.0.0.1:{port}/first"

    with pytest.raises(SystemExit) as exc:
        mod.fetch_quota(SYNTHETIC_TOKEN, url=url)
    assert exc.value.code == 1
    assert _CountingHandler.hits == ["/first"]


class _OtherHostHandler(http.server.BaseHTTPRequestHandler):
    """Stands in for a wholly different authority -- any hit here means a
    cross-host redirect got followed."""

    hits = []

    def do_GET(self):
        _OtherHostHandler.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_redirect_to_cross_host_url_is_never_followed(mod, redirect_server):
    """A Location pointing at a different authority entirely (a second,
    independent server on its own port) must be rejected exactly like a
    same-host one -- and that other server must never see a request."""
    _OtherHostHandler.hits = []
    other_server, other_thread = _start_server(_OtherHostHandler)
    try:
        other_port = other_server.server_address[1]
        _CountingHandler.redirect_code = 302
        _CountingHandler.location = f"http://127.0.0.1:{other_port}/redirected"
        port = redirect_server.server_address[1]
        url = f"http://127.0.0.1:{port}/first"

        with pytest.raises(SystemExit) as exc:
            mod.fetch_quota(SYNTHETIC_TOKEN, url=url)
        assert exc.value.code == 1
        assert _CountingHandler.hits == ["/first"]
        assert _OtherHostHandler.hits == []
    finally:
        other_server.shutdown()
        other_thread.join(timeout=5)


def test_untrusted_certificate_fails_without_insecure_retry(mod, tmp_path):
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-days", "1", "-nodes",
            "-subj", "/CN=127.0.0.1",
            "-keyout", str(key_path), "-out", str(cert_path),
        ],
        check=True,
        capture_output=True,
    )

    _CountingHandler.hits = []
    _CountingHandler.redirect_code = 302  # unused; server always 200s here
    server = http.server.HTTPServer(("127.0.0.1", 0), _CountingHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        url = f"https://127.0.0.1:{port}/first"
        with pytest.raises(SystemExit) as exc:
            mod.fetch_quota(SYNTHETIC_TOKEN, url=url)
        assert exc.value.code == 1
        # A trust-chain failure happens during the TLS handshake, before the
        # HTTP layer -- the self-signed server should never see a request.
        assert _CountingHandler.hits == []
    finally:
        server.shutdown()
        thread.join(timeout=5)


# --------------------------------------------------------------- end to end

def test_main_success_prints_one_json_record_and_exits_zero(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "get_token", lambda: SYNTHETIC_TOKEN)
    monkeypatch.setattr(mod, "fetch_quota", lambda token: SAMPLE_PAYLOAD)

    mod.main()  # returns normally (exit 0) on success -- no SystemExit

    out = capsys.readouterr()
    record = json.loads(out.out)
    assert record["plan"] == "enterprise"
    assert SYNTHETIC_TOKEN not in out.out
    assert SYNTHETIC_TOKEN not in out.err


@pytest.fixture()
def failing_gh(tmp_path):
    """A directory on PATH holding a `gh` that always reports not-authenticated."""
    gh_dir = tmp_path / "fake-gh-bin"
    gh_dir.mkdir()
    gh_path = gh_dir / "gh"
    gh_path.write_text("#!/bin/sh\necho not-authenticated 1>&2\nexit 1\n")
    gh_path.chmod(0o755)
    return gh_dir


@pytest.fixture()
def isolated_dirs(tmp_path):
    """HOME/XDG_*/TMPDIR-shaped directories, isolated from the real ones."""
    dirs = {name: tmp_path / name for name in ("home", "config", "cache", "state", "tmp")}
    for d in dirs.values():
        d.mkdir()
    return dirs


def _snapshot(dirs):
    return sorted(p for d in dirs.values() for p in d.rglob("*"))


# Single source of truth for which isolated_dirs entry backs which env var --
# shared by _isolated_env() (subprocess runs) and the in-process normal-path
# test (monkeypatch.setenv), so the two never drift apart.
ISOLATION_ENV_VARS = {
    "HOME": "home",
    "XDG_CONFIG_HOME": "config",
    "XDG_CACHE_HOME": "cache",
    "XDG_STATE_HOME": "state",
    "TMPDIR": "tmp",
}


def _isolated_env(gh_dir, dirs):
    env = {var: str(dirs[key]) for var, key in ISOLATION_ENV_VARS.items()}
    env["PATH"] = str(gh_dir)
    return env


def test_main_via_subprocess_never_leaks_token_or_auth_header(failing_gh):
    """Runs the real script as a subprocess (gh mocked out via PATH) to
    confirm no marker escapes stdout/stderr end-to-end."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env={"PATH": str(failing_gh)},
    )
    assert result.returncode == 1
    assert SYNTHETIC_TOKEN not in result.stdout
    assert SYNTHETIC_TOKEN not in result.stderr
    assert AUTH_MARKER not in result.stderr
    assert result.stdout == ""


def test_no_filesystem_writes_under_isolated_directories(failing_gh, isolated_dirs):
    """Acceptance criterion 14: run with isolated HOME/config/cache/state/tmp
    dirs (gh forced to fail, so there's no network dependency here -- the
    successful-fetch path is covered by the "normal path" test below) and
    confirm the collector writes nothing there. The script has no
    open()-for-write call at all, so this should hold trivially, but the
    point is to observe it, not assume it from reading the source."""
    before = _snapshot(isolated_dirs)
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env=_isolated_env(failing_gh, isolated_dirs),
        timeout=15,
    )
    after = _snapshot(isolated_dirs)

    assert before == after, f"unexpected filesystem writes: {set(after) - set(before)}"
    for path in after:
        if path.is_file():
            assert SYNTHETIC_TOKEN not in path.read_text(errors="ignore")
    assert result.returncode == 1
    assert SYNTHETIC_TOKEN not in result.stdout
    assert SYNTHETIC_TOKEN not in result.stderr


def test_no_filesystem_writes_under_isolated_directories_normal_path(mod, monkeypatch, isolated_dirs):
    """Complements the forced-failure case above with the 'normal' (gh
    succeeds) path. main()'s URL is a hardcoded constant per spec.md's
    Security section, so it can't be redirected to a test server without
    changing production code -- get_token/fetch_quota are mocked instead,
    keeping the constant real while still observing no writes occur."""
    for var, key in ISOLATION_ENV_VARS.items():
        monkeypatch.setenv(var, str(isolated_dirs[key]))
    monkeypatch.setattr(mod, "get_token", lambda: SYNTHETIC_TOKEN)
    monkeypatch.setattr(mod, "fetch_quota", lambda token: SAMPLE_PAYLOAD)

    before = _snapshot(isolated_dirs)
    mod.main()
    after = _snapshot(isolated_dirs)

    assert before == after, f"unexpected filesystem writes: {set(after) - set(before)}"
