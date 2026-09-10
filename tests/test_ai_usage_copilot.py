"""Tests for bin/ai-usage-copilot.

Covers spec.md's acceptance criteria: the pinned gh invocation (argv/env), redirect and
TLS-certificate failures exercised through the real opener (not a stubbed-out one), that no
synthetic token/marker ever reaches stdout, stderr, a subprocess argument/environment, or the
local history cache file, and the daily-credits-history bookkeeping (sampling, delta
computation, billing-period-reset clamping, retention pruning, and the file lock) introduced for
the Copilot tab's "credits by day" chart.
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


# -------------------------------------------------------------- parse_quota

def test_parse_quota_sums_credits_used_across_categories(mod):
    quota = mod.parse_quota(SAMPLE_PAYLOAD)
    assert quota["plan"] == "enterprise"
    assert quota["quotaResetDate"] == "2026-10-01"
    assert quota["totalCreditsUsed"] == 11807


def test_parse_quota_missing_field_fails_without_leaking_payload(mod, capsys):
    broken = {"copilot_plan": "enterprise"}  # no quota_snapshots
    with pytest.raises(SystemExit) as exc:
        mod.parse_quota(broken)
    assert exc.value.code == 1
    out = capsys.readouterr()
    assert "enterprise" not in out.err


# ------------------------------------------------------- fetch_quota (mocked)
# These failure modes (malformed JSON, generic HTTP error) aren't the
# redirect/TLS cases spec.md's acceptance criterion requires a real
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
# These two cases exercise the actual production opener
# (build_opener(NoRedirectHandler) + default handlers), not a stub -- so they
# run a real local server over a real socket.

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


# --------------------------------------------------- daily-history bookkeeping

def test_compute_recent_days_returns_deltas_between_consecutive_samples(mod):
    history = {"2026-09-07": 100, "2026-09-08": 130, "2026-09-09": 175}
    days = mod.compute_recent_days(history)
    assert days == [
        {"date": "2026-09-08", "creditsUsed": 30},
        {"date": "2026-09-09", "creditsUsed": 45},
    ]


def test_compute_recent_days_omits_the_earliest_entry_with_no_baseline(mod):
    history = {"2026-09-09": 175}
    assert mod.compute_recent_days(history) == []


def test_compute_recent_days_clamps_a_billing_period_reset_to_the_raw_value(mod):
    # Cumulative total dropped from 175 to 12 -- a quota-period reset, not negative usage.
    history = {"2026-09-09": 175, "2026-09-10": 12}
    assert mod.compute_recent_days(history) == [{"date": "2026-09-10", "creditsUsed": 12}]


def test_compute_recent_days_caps_at_the_last_seven(mod):
    # 9 samples -> 8 consecutive deltas; only the most recent 7 are returned.
    history = {f"2026-09-{d:02d}": d * 10 for d in range(1, 10)}
    days = mod.compute_recent_days(history)
    assert len(days) == 7
    assert days[0]["date"] == "2026-09-03"
    assert days[-1]["date"] == "2026-09-09"


def test_compute_recent_days_absorbs_a_multi_day_gap_into_one_delta(mod):
    # No sample for 09-08/09-09 (machine off) -- the next delta spans the gap.
    history = {"2026-09-07": 100, "2026-09-10": 220}
    assert mod.compute_recent_days(history) == [{"date": "2026-09-10", "creditsUsed": 120}]


def test_prune_history_keeps_only_the_most_recent_n_dates(mod):
    history = {f"2026-09-{d:02d}": d for d in range(1, 12)}  # 11 distinct dates
    pruned = mod.prune_history(history, keep=8)
    assert sorted(pruned) == [f"2026-09-{d:02d}" for d in range(4, 12)]


def test_history_path_respects_xdg_cache_home(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert mod.history_path() == tmp_path / "bwright.ai-usage" / "copilot-history.json"


@pytest.fixture()
def history_file(tmp_path):
    """An arbitrary path under tmp_path shaped like a real history cache file --
    for tests exercising sample_history()/recent_days_for() directly, as opposed
    to test_history_path_respects_xdg_cache_home above, which tests that shape
    is what history_path() itself actually produces."""
    return tmp_path / "bwright.ai-usage" / "copilot-history.json"


def test_sample_history_creates_file_and_records_todays_total(mod, history_file):
    history = mod.sample_history(history_file, "2026-09-10", 42)
    assert history == {"2026-09-10": 42}
    assert json.loads(history_file.read_text()) == {"2026-09-10": 42}


def test_sample_history_overwrites_same_day_with_the_latest_total(mod, history_file):
    mod.sample_history(history_file, "2026-09-10", 10)
    history = mod.sample_history(history_file, "2026-09-10", 25)
    assert history == {"2026-09-10": 25}


def test_sample_history_prunes_while_writing(mod, history_file):
    for day in range(1, 10):
        history = mod.sample_history(history_file, f"2026-09-{day:02d}", day, keep=8)
    assert sorted(history) == [f"2026-09-{d:02d}" for d in range(2, 10)]


def test_sample_history_survives_a_corrupt_existing_file(mod, history_file):
    history_file.parent.mkdir(parents=True)
    history_file.write_text("{not json")
    history = mod.sample_history(history_file, "2026-09-10", 5)
    assert history == {"2026-09-10": 5}


def test_sample_history_takes_an_exclusive_lock(mod, history_file, monkeypatch):
    calls = []
    real_flock = mod.fcntl.flock

    def spy_flock(fd, op):
        calls.append(op)
        return real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", spy_flock)
    mod.sample_history(history_file, "2026-09-10", 5)

    assert mod.fcntl.LOCK_EX in calls
    assert mod.fcntl.LOCK_UN in calls


def test_recent_days_for_computes_from_a_fresh_history_file(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    first = mod.recent_days_for(100, today="2026-09-09")
    assert first == []  # no prior baseline yet
    second = mod.recent_days_for(150, today="2026-09-10")
    assert second == [{"date": "2026-09-10", "creditsUsed": 50}]


def test_recent_days_for_degrades_to_empty_list_when_cache_is_unwritable(mod, monkeypatch):
    def raise_always(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(mod, "sample_history", raise_always)
    assert mod.recent_days_for(100, today="2026-09-10") == []


def test_recent_days_for_never_writes_the_token(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    mod.recent_days_for(100, today="2026-09-10")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SYNTHETIC_TOKEN not in path.read_text(errors="ignore")


# --------------------------------------------------------------- end to end

def test_main_success_prints_one_json_record_and_exits_zero(mod, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(mod, "get_token", lambda: SYNTHETIC_TOKEN)
    monkeypatch.setattr(mod, "fetch_quota", lambda token: SAMPLE_PAYLOAD)

    mod.main()  # returns normally (exit 0) on success -- no SystemExit

    out = capsys.readouterr()
    record = json.loads(out.out)
    assert record["plan"] == "enterprise"
    assert "categories" not in record
    assert record["recentDays"] == []
    assert record["todayCreditsUsed"] == -1  # no prior-day baseline yet -- first run
    assert SYNTHETIC_TOKEN not in out.out
    assert SYNTHETIC_TOKEN not in out.err


def test_main_reports_todays_credits_used_once_a_prior_sample_exists(mod, monkeypatch, capsys, tmp_path, history_file):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(mod, "get_token", lambda: SYNTHETIC_TOKEN)
    monkeypatch.setattr(mod, "fetch_quota", lambda token: SAMPLE_PAYLOAD)
    monkeypatch.setattr(mod, "local_today_string", lambda: "2026-09-10")
    mod.sample_history(history_file, "2026-09-09", 11800)

    mod.main()

    record = json.loads(capsys.readouterr().out)
    assert record["recentDays"] == [{"date": "2026-09-10", "creditsUsed": 7}]
    assert record["todayCreditsUsed"] == 7


def test_main_success_writes_todays_sample_to_the_history_cache(mod, monkeypatch, tmp_path, history_file):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(mod, "get_token", lambda: SYNTHETIC_TOKEN)
    monkeypatch.setattr(mod, "fetch_quota", lambda token: SAMPLE_PAYLOAD)

    mod.main()

    assert history_file.is_file()
    history = json.loads(history_file.read_text())
    assert list(history.values()) == [11807]
    assert SYNTHETIC_TOKEN not in history_file.read_text()


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


def test_no_filesystem_writes_on_the_credential_failure_path(failing_gh, isolated_dirs):
    """A failed gh lookup exits before any quota is fetched, so there is
    nothing yet to sample into the history cache -- run with isolated
    HOME/config/cache/state/tmp dirs and confirm the collector writes
    nothing there. (The successful-fetch path *does* write the history
    cache now -- see test_main_success_writes_todays_sample_to_the_history_cache
    and test_no_filesystem_writes_beyond_the_history_cache_on_success below.)"""
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


def test_no_filesystem_writes_beyond_the_history_cache_on_success(mod, monkeypatch, isolated_dirs):
    """Complements the forced-failure case above with the 'normal' (gh
    succeeds) path. main()'s URL is a hardcoded constant per spec.md's
    Security section, so it can't be redirected to a test server without
    changing production code -- get_token/fetch_quota are mocked instead,
    keeping the constant real while still observing what gets written.
    The only write allowed anywhere under the isolated dirs is the history
    cache file itself, and it must never contain the token."""
    for var, key in ISOLATION_ENV_VARS.items():
        monkeypatch.setenv(var, str(isolated_dirs[key]))
    monkeypatch.setattr(mod, "get_token", lambda: SYNTHETIC_TOKEN)
    monkeypatch.setattr(mod, "fetch_quota", lambda token: SAMPLE_PAYLOAD)

    before = _snapshot(isolated_dirs)
    mod.main()
    after = _snapshot(isolated_dirs)

    new_paths = set(after) - set(before)
    history_file = isolated_dirs["cache"] / "bwright.ai-usage" / "copilot-history.json"
    assert new_paths <= {history_file.parent, history_file}, f"unexpected writes: {new_paths}"
    for path in after:
        if path.is_file():
            assert SYNTHETIC_TOKEN not in path.read_text(errors="ignore")
