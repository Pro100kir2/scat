"""deploy.sh, function by function: repository over SSH, the setup marker and mode selection.

deploy.sh is sourced (its main only runs when executed), system commands are stubbed with
shell functions, and state goes to a temporary directory. deploy.sh targets Debian/Ubuntu,
so these tests need Linux with GNU coreutils, bash and git; elsewhere they are skipped.
Run them in a container:  docker run --rm -v "$PWD:/src" -w /src python:3.13-slim sh -c
  'apt-get update -q && apt-get install -yq git openssh-client && pip install -q pytest && pytest tests/test_deploy.py'
"""
import platform
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy.sh"

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or not shutil.which("git") or not shutil.which("bash"),
    reason="deploy.sh runs on Debian/Ubuntu: needs Linux, GNU coreutils, bash and git",
)

# Every step of a deployment, replaced by a stub that records the call and marks success.
STUB_STEPS = """
CALLS=()
record() { CALLS+=("$1"); echo "CALL $1" >>"$TMP/calls"; }
inspect_existing() { record inspect_existing; }
install_base() { record install_base; mark Git OK; }
install_docker() { record install_docker; mark Docker OK; mark "Docker Compose" OK; }
sync_repository() { record sync_repository; mark Repository OK; }
setup_env() { record setup_env; mark Environment OK; }
deploy_containers() { record "deploy_containers:$1"; mark "Backend container" OK; mark "Frontend container" OK; }
configure_caddy() { record configure_caddy; mark Caddy OK; }
verify_caddy() { record verify_caddy; mark Caddy OK; }
configure_firewall() { record configure_firewall; mark Firewall OK; }
check_dns() { record check_dns; mark DNS OK; }
health_checks() { record health_checks; mark HTTPS OK; mark Frontend OK; mark API OK; }
detect_os() { :; }
summary() { :; }
"""

# The live-server checks of is_initialized, all passing (a healthy production stack).
HEALTHY_SERVER = """
st_repo() { :; }; st_env() { :; }; st_docker() { :; }; st_compose() { :; }; st_backend() { :; }
st_frontend() { :; }; st_loopback_only() { :; }; st_caddy_installed() { :; }; st_caddy_service() { :; }
st_caddy_config() { :; }; st_caddy_validate() { :; }; st_https() { :; }
"""


def bash(tmp_path: Path, script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Source deploy.sh with all state under tmp_path, then run `script`."""
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    if not (app / ".git").exists():
        subprocess.run(["git", "init", "-q", str(app)], check=True)
        subprocess.run(["git", "-C", str(app), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    prelude = f"""
set -Eeuo pipefail
TMP={tmp_path}
source {DEPLOY}
APP_DIR={app}
STATE_DIR={tmp_path}/state
STATE_FILE=$STATE_DIR/.initialized
GITHUB_KNOWN_HOSTS=$STATE_DIR/github_known_hosts
LOG_FILE={tmp_path}/deploy.log
: >"$LOG_FILE"
CADDY_SITE_FILE={tmp_path}/scat.caddy
[[ -f "$CADDY_SITE_FILE" ]] || echo 'example.com {{ }}' >"$CADDY_SITE_FILE"
[[ -f "$APP_DIR/.env" ]] || {{ echo 'COMPOSE_PROJECT_NAME=scat' >"$APP_DIR/.env"; chmod 600 "$APP_DIR/.env"; }}
"""
    full_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}
    full_env.update(env or {})
    return subprocess.run(["bash", "-c", prelude + textwrap.dedent(script)], capture_output=True, text=True,
                          env=full_env, timeout=60)


def marker(tmp_path: Path) -> dict:
    path = tmp_path / "state" / ".initialized"
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


# --------------------------------------------------------------------------- SSH repository
def test_default_remote_is_ssh_with_the_exact_owner(tmp_path):
    out = bash(tmp_path, 'echo "$REPO_URL|$(repo_path)|$(repo_host)"').stdout.strip()
    assert out == "git@github.com:Pro100Kir2/scat.git|Pro100Kir2/scat|github.com"


def test_scat_repo_url_override(tmp_path):
    out = bash(tmp_path, 'echo "$REPO_URL|$(repo_path)"', env={"SCAT_REPO_URL": "git@github.com:Fork/scat.git"})
    assert out.stdout.strip() == "git@github.com:Fork/scat.git|Fork/scat"


def test_no_token_based_github_authentication_is_left():
    text = DEPLOY.read_text()
    for gone in ("prepare_token", "git_auth", "GIT_ASKPASS", "askPass", "SCAT_GIT_TOKEN", "x-access-token",
                 "GitHub token (input hidden)", "https://github.com/Pro100kir2/scat.git"):
        assert gone not in text, gone
    # The only token mentions left keep a stray token out of .env (it reaches the container).
    assert [l for l in text.splitlines() if "GITHUB_TOKEN" in l and "grep" not in l] == []


@pytest.mark.parametrize("url", [
    "git@github.com:Pro100Kir2/scat.git",
    "git@github.com:pro100kir2/scat",
    "https://github.com/Pro100kir2/scat.git",
    "https://github.com/Pro100Kir2/scat/",
    "ssh://git@github.com/Pro100Kir2/scat.git",
    "https://x-access-token:SECRET@github.com/Pro100kir2/scat.git",
])
def test_repo_slug_is_transport_and_case_insensitive(tmp_path, url):
    assert bash(tmp_path, f'repo_slug "{url}"').stdout.strip() == "github.com/pro100kir2/scat"


def test_st_repo(tmp_path):
    cases = {
        "git@github.com:Pro100Kir2/scat.git": 0,
        "https://github.com/Pro100kir2/scat.git": 0,          # an HTTPS checkout: UPDATE moves it to SSH
        "https://tok@github.com/Pro100kir2/scat.git": 1,       # embedded credentials: SETUP scrubs them
        "git@github.com:someone/else.git": 1,
    }
    for url, expected in cases.items():
        res = bash(tmp_path, f"""
            git -C "$APP_DIR" remote remove origin 2>/dev/null || true
            git -C "$APP_DIR" remote add origin '{url}'
            st_repo && echo yes || echo no
        """)
        assert res.stdout.strip() == ("yes" if expected == 0 else "no"), (url, res.stderr)
    assert bash(tmp_path, 'rm -rf "$APP_DIR/.git"; st_repo && echo yes || echo no').stdout.strip() == "no"


def test_git_ssh_command_is_non_interactive_and_verifies_the_host(tmp_path):
    res = bash(tmp_path, """
        SSH_KEY=$TMP/key; echo "without: $(git_ssh_command)"
        touch "$SSH_KEY"; echo "with: $(git_ssh_command)"
    """)
    without, with_key = res.stdout.strip().splitlines()
    for opt in ("BatchMode=yes", "StrictHostKeyChecking=yes", f"UserKnownHostsFile={tmp_path}/state/github_known_hosts"):
        assert opt in without and opt in with_key
    assert " -i " not in without and f"-i {tmp_path}/key" in with_key
    # With a deploy key it is the only key offered; without one, root's keys and agent are used.
    assert "IdentitiesOnly=yes" in with_key and "IdentitiesOnly" not in without


META = '{"verifiable_password_authentication":false,"ssh_key_fingerprints":{"SHA256_ED25519":"+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"},"ssh_keys":["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl","ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg="]}'


def test_github_host_keys_come_from_the_api(tmp_path):
    res = bash(tmp_path, f"""
        curl() {{ printf '%s' '{META}'; }}
        refresh_github_known_hosts; cat "$GITHUB_KNOWN_HOSTS"
    """)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.strip().splitlines()
    assert lines == ["github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
                     "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg="]


def test_github_host_keys_fall_back_to_the_saved_copy_or_stop(tmp_path):
    kept = bash(tmp_path, """
        mkdir -p "$STATE_DIR"; echo 'github.com ssh-ed25519 SAVED' >"$GITHUB_KNOWN_HOSTS"
        curl() { return 7; }
        refresh_github_known_hosts; cat "$GITHUB_KNOWN_HOSTS"
    """)
    assert kept.returncode == 0 and "SAVED" in kept.stdout and "using the saved GitHub SSH host keys" in kept.stdout
    stopped = bash(tmp_path, 'rm -f "$GITHUB_KNOWN_HOSTS"; curl() { return 7; }; refresh_github_known_hosts')
    assert stopped.returncode == 1 and "api.github.com/meta" in stopped.stderr


def test_ensure_git_access_ok(tmp_path):
    res = bash(tmp_path, f"""
        curl() {{ printf '%s' '{META}'; }}
        git() {{ [[ "$*" == *"ls-remote --heads git@github.com:Pro100Kir2/scat.git"* ]] || return 9
                 [[ "$GIT_SSH_COMMAND" == *BatchMode=yes* && "$GIT_TERMINAL_PROMPT" == 0 ]] || return 8; echo "abc refs/heads/main"; }}
        ensure_git_access
    """)
    assert res.returncode == 0, res.stderr
    assert "SSH access to git@github.com:Pro100Kir2/scat.git" in res.stdout


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh-keygen (openssh-client)")
def test_ensure_git_access_creates_a_deploy_key_and_stops_with_instructions(tmp_path):
    res = bash(tmp_path, f"""
        SSH_KEY=$TMP/ssh/scat_deploy_ed25519
        curl() {{ printf '%s' '{META}'; }}
        git() {{ echo 'git@github.com: Permission denied (publickey).' >&2; return 128; }}
        ensure_git_access
    """)
    assert res.returncode == 1
    key = tmp_path / "ssh" / "scat_deploy_ed25519"
    assert key.exists() and (key.stat().st_mode & 0o777) == 0o600
    pub = (tmp_path / "ssh" / "scat_deploy_ed25519.pub").read_text().strip()
    assert pub.startswith("ssh-ed25519 ") and pub in res.stderr
    assert "https://github.com/Pro100Kir2/scat/settings/keys" in res.stderr
    assert "BEGIN OPENSSH PRIVATE KEY" not in res.stdout + res.stderr


def make_remote(tmp_path: Path) -> Path:
    """A local bare repository with the files sync_repository requires."""
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for name in ("Dockerfile", "docker-compose.yml", "docker-compose.frontend.yml", "frontend/Dockerfile",
                 "frontend/nginx.conf", "frontend/package-lock.json", ".env.example", "config.txt"):
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text("x\n")
    (work / ".gitignore").write_text(".env\n")
    git = ["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "files"], check=True)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare


def test_sync_repository_clones_and_fast_forwards(tmp_path):
    bare = make_remote(tmp_path)
    res = bash(tmp_path, f"""
        REPO_URL={bare}
        rm -rf "$APP_DIR"
        sync_repository; echo "changed=$REPO_CHANGED origin=$(git -C "$APP_DIR" remote get-url origin)"
        sync_repository; echo "changed=$REPO_CHANGED"
    """)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"changed=1 origin={bare}" in res.stdout and res.stdout.strip().endswith("changed=0")


def test_sync_repository_moves_origin_and_scrubs_legacy_tokens(tmp_path):
    bare = make_remote(tmp_path)
    res = bash(tmp_path, f"""
        REPO_URL={bare}
        rm -rf "$APP_DIR"; git clone -q {bare} "$APP_DIR"
        git -C "$APP_DIR" remote set-url origin 'https://x-access-token:OLD_TOKEN@github.com/Pro100kir2/scat.git'
        git -C "$APP_DIR" config http.https://github.com/.extraheader 'AUTHORIZATION: basic OLD'
        sync_repository
        echo "origin=$(git -C "$APP_DIR" remote get-url origin)"
        git -C "$APP_DIR" config --get-regexp extraheader || echo "no-extraheader"
        grep -c OLD_TOKEN "$APP_DIR/.git/config" || true
    """)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"origin={bare}" in res.stdout and "no-extraheader" in res.stdout
    assert res.stdout.strip().endswith("0")


# --------------------------------------------------------------------------- DNS
def test_check_dns_pending_warns_instead_of_aborting(tmp_path):
    """Regression: `[[ STRICT_DNS == 1 ]] && die` as the last command made set -e abort SETUP."""
    res = bash(tmp_path, """
        public_ip() { [[ "$1" == 4 ]] && echo 203.0.113.9 || true; }
        getent() { echo "198.51.100.1 STREAM $2"; }
        check_dns
        echo "continued DNS_READY=$DNS_READY DNS=${STATUS[DNS]}"
    """)
    assert res.returncode == 0, res.stderr
    assert "continued DNS_READY=0 DNS=PENDING" in res.stdout
    strict = bash(tmp_path, """
        public_ip() { [[ "$1" == 4 ]] && echo 203.0.113.9 || true; }
        getent() { echo "198.51.100.1 STREAM $2"; }
        check_dns
    """, env={"STRICT_DNS": "1"})
    assert strict.returncode == 1 and "DNS is not ready (STRICT_DNS=1)" in strict.stderr


# DNS points elsewhere (198.51.100.1) than this server (203.0.113.9).
WRONG_DNS = """
public_ip() { [[ "$1" == 4 ]] && echo 203.0.113.9 || true; }
getent() { echo "198.51.100.1 STREAM $2"; }
"""

# Caddy on this server serves the whole stack with valid certificates (the --resolve checks pass).
LOCAL_TLS_OK = """
https_verified() { HTTPS_CODE=200; return 0; }
http_code() { case "$*" in *deep/link*) echo 404 ;; *admin*) echo 401 ;; *docs*) echo 404 ;; *http://*) echo 308 ;; *) echo 200 ;; esac; }
lcurl() { case "$*" in
    *-D*) [[ "$*" == *evil* ]] || printf 'access-control-allow-origin: https://%s\\r\\n' "$DOMAIN" ;;
    *-sI*www*) printf 'location: https://%s/\\r\\n' "$DOMAIN" ;;
    *-sI*) printf 'strict-transport-security: max-age=1\\r\\n' ;;
    *assets*) echo "https://$API_DOMAIN" ;;
    *api/results*) echo '{"cache_state":"fresh","nodes":[]}' ;;
    *api/scan/status*) echo '{"cache_state":"fresh"}' ;;
    *api/health*) echo '{"status":"ok"}' ;;
    *) echo '<div id="app"></div><script src="/assets/index-AbC123.js"></script>' ;;
  esac; }
openssl() { :; }; journalctl() { :; }
curl() { return 7; }   # the public path from inside (hairpin) does not work
"""

# Every step stubbed except the real check_dns and health_checks.
STUB_STEPS_REAL_CHECKS = "\n".join(
    line for line in STUB_STEPS.splitlines() if not line.startswith(("check_dns()", "health_checks()"))
)


def test_valid_local_tls_does_not_make_wrong_dns_ready(tmp_path):
    """The local checks still validate the stack, but DNS stays a separate, unmet requirement."""
    res = bash(tmp_path, WRONG_DNS + LOCAL_TLS_OK + """
        for k in Repository Environment "Backend container" "Frontend container" Caddy Firewall; do mark "$k" OK; done
        check_dns
        health_checks
        echo "DNS=${STATUS[DNS]} HTTPS=${STATUS[HTTPS]} Frontend=${STATUS[Frontend]} API=${STATUS[API]}"
        critical_checks_passed && echo "complete" || echo "incomplete: ${CRITICAL_MISSING[*]}"
    """)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "DNS=PENDING HTTPS=OK Frontend=OK API=OK" in res.stdout   # stack validated through Caddy
    assert "incomplete: DNS=PENDING" in res.stdout
    assert "DNS does not point to this server" in res.stdout


def test_dns_mismatch_with_valid_local_tls_ends_setup_incomplete_without_marker(tmp_path):
    """Real check_dns + health_checks inside a real SETUP: exit 3, and write_marker never runs."""
    res = bash(tmp_path, STUB_STEPS_REAL_CHECKS + HEALTHY_SERVER + WRONG_DNS + LOCAL_TLS_OK + """
        write_marker() { echo "WRITE_MARKER CALLED"; exit 99; }
        main
    """)
    assert res.returncode == 3, res.stdout + res.stderr
    assert "WRITE_MARKER CALLED" not in res.stdout
    assert "SETUP INCOMPLETE" in res.stdout
    assert not (tmp_path / "state" / ".initialized").exists()
    assert "RESULT: SETUP INCOMPLETE (DNS=PENDING)" in (tmp_path / "deploy.log").read_text()


def test_setup_completes_once_dns_points_here(tmp_path):
    """The same server with correct DNS: the marker is written and the next run is an UPDATE."""
    right_dns = WRONG_DNS.replace("198.51.100.1", "203.0.113.9")
    first = bash(tmp_path, STUB_STEPS_REAL_CHECKS + HEALTHY_SERVER + right_dns + LOCAL_TLS_OK + "main")
    assert first.returncode == 0, first.stdout + first.stderr
    assert marker(tmp_path)["mode"] == "SETUP"
    second = bash(tmp_path, STUB_STEPS_REAL_CHECKS + HEALTHY_SERVER + right_dns + LOCAL_TLS_OK + "main")
    assert second.returncode == 0 and "Mode: UPDATE" in second.stdout


def test_dns_mismatch_keeps_an_update_from_refreshing_the_marker(tmp_path):
    right_dns = WRONG_DNS.replace("198.51.100.1", "203.0.113.9")
    assert bash(tmp_path, STUB_STEPS_REAL_CHECKS + HEALTHY_SERVER + right_dns + LOCAL_TLS_OK + "main").returncode == 0
    before = marker(tmp_path)
    res = bash(tmp_path, STUB_STEPS_REAL_CHECKS + HEALTHY_SERVER + WRONG_DNS + LOCAL_TLS_OK + "main")
    assert res.returncode == 1 and "Mode: UPDATE" in res.stdout
    assert "these checks did not pass: DNS=PENDING" in res.stderr
    assert marker(tmp_path) == before  # neither refreshed nor deleted


# --------------------------------------------------------------------------- marker
def test_critical_checks_passed_names_what_is_missing(tmp_path):
    res = bash(tmp_path, """
        for k in Repository Environment "Backend container" "Frontend container" Caddy Firewall HTTPS Frontend API; do mark "$k" OK; done
        mark DNS "PENDING"
        critical_checks_passed && echo passed || echo "missing: ${CRITICAL_MISSING[*]}"
        mark DNS OK; critical_checks_passed && echo passed
    """)
    assert res.stdout.strip().splitlines() == ["missing: DNS=PENDING", "passed"]


def test_write_marker_and_what_it_is_bound_to(tmp_path):
    res = bash(tmp_path, """
        MODE=SETUP; write_marker
        stat -c '%a' "$STATE_FILE"
        st_marker && st_marker_matches && echo matches
        SELF_SHA=0000000000000000000000000000000000000000000000000000000000000000
        st_marker_matches && echo "still matches after deploy.sh changed"
        STATE_SCHEMA=2; st_marker_matches || echo "schema bump invalidates"
        STATE_SCHEMA=1; DOMAIN=other.example; st_marker_matches || echo "other domain invalidates"
    """)
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() [0] == "600"
    for line in ("matches", "still matches after deploy.sh changed", "schema bump invalidates", "other domain invalidates"):
        assert line in res.stdout
    m = marker(tmp_path)
    assert m["status"] == "ok" and m["state_schema"] == "1" and m["mode"] == "SETUP"
    assert len(m["commit"]) == 40 and len(m["env_sha256"]) == 64 and len(m["caddy_site_sha256"]) == 64
    assert m["domain"] == "scat-search.ru" and m["api_domain"] == "api.scat-search.ru"
    assert "RESULT: SETUP OK, marker" in (tmp_path / "deploy.log").read_text()


def test_old_markers_without_a_schema_do_not_match(tmp_path):
    res = bash(tmp_path, """
        mkdir -p "$STATE_DIR"
        printf 'status=ok\\ndeploy_sha256=%s\\ndomain=%s\\napi_domain=%s\\napp_dir=%s\\n' "$SELF_SHA" "$DOMAIN" "$API_DOMAIN" "$APP_DIR" >"$STATE_FILE"
        st_marker && echo "marker ok"; st_marker_matches || echo "needs one SETUP"
    """)
    assert res.stdout.split("\n")[:2] == ["marker ok", "needs one SETUP"]


def test_write_marker_fails_loudly_and_writes_nothing(tmp_path):
    res = bash(tmp_path, 'MODE=SETUP; rm -rf "$APP_DIR/.git"; write_marker')
    assert res.returncode == 1
    assert "cannot read the deployed commit for the marker" in res.stderr
    assert "Marker: not written (SETUP failed)" in res.stderr
    assert not (tmp_path / "state" / ".initialized").exists()


# --------------------------------------------------------------------------- modes
def test_successful_setup_writes_the_marker_and_the_next_run_updates(tmp_path):
    first = bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + "main; echo 'exit=0'")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Mode: SETUP" in first.stdout and "marker written" in first.stdout
    assert marker(tmp_path)["mode"] == "SETUP"

    second = bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + "main")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Mode: UPDATE (server matches the expected state)" in second.stdout
    assert marker(tmp_path)["mode"] == "UPDATE"
    assert "CALL verify_caddy" in (tmp_path / "calls").read_text()


def test_editing_deploy_sh_does_not_force_setup(tmp_path):
    assert bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + "main").returncode == 0
    res = bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + "SELF_SHA=$(printf x | sha256sum | cut -d' ' -f1); main")
    assert "Mode: UPDATE" in res.stdout, res.stdout


def test_setup_order_marker_is_only_invalidated_after_the_repository_step(tmp_path):
    """An older deploy.sh hands over inside sync_repository; it must not delete the marker first."""
    assert bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + "main").returncode == 0
    res = bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + """
        sync_repository() { record sync_repository; exit 0; }   # like the exec of the checkout's deploy.sh
        FORCE_SETUP=1; main
    """)
    assert res.returncode == 0
    assert marker(tmp_path)["status"] == "ok"  # untouched
    calls = (tmp_path / "calls").read_text().split("CALL ")
    assert calls[-3:] == ["inspect_existing\n", "install_base\n", "sync_repository\n"]


def test_failed_setup_writes_no_marker_and_logs_why(tmp_path):
    res = bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + """
        health_checks() { COMPONENT=API; die "GET /api/health failed"; }
        main
    """)
    assert res.returncode == 1
    assert not (tmp_path / "state" / ".initialized").exists()
    log = (tmp_path / "deploy.log").read_text()
    assert "RESULT: SETUP FAILED at API" in log
    assert "Marker: not written (SETUP failed)" in res.stderr


def test_setup_waiting_for_dns_is_incomplete_not_successful(tmp_path):
    res = bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + """
        check_dns() { record check_dns; mark DNS PENDING; }
        health_checks() { mark HTTPS "PENDING (DNS)"; mark Frontend "PENDING (DNS)"; mark API "PENDING (DNS)"; }
        main
    """)
    assert res.returncode == 3
    assert "SETUP INCOMPLETE" in res.stdout
    assert not (tmp_path / "state" / ".initialized").exists()
    assert "RESULT: SETUP INCOMPLETE (DNS=PENDING HTTPS=PENDING (DNS)" in (tmp_path / "deploy.log").read_text()


def test_failed_update_keeps_the_marker(tmp_path):
    assert bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + "main").returncode == 0
    before = marker(tmp_path)
    res = bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + """
        deploy_containers() { COMPONENT="Backend container"; die "backend did not become healthy"; }
        main
    """)
    assert res.returncode == 1 and "Mode: UPDATE" in res.stdout
    assert marker(tmp_path) == before
    assert "Marker: kept from the last successful deployment" in res.stderr
    # The server itself is still healthy, so the next run is an UPDATE again, not a SETUP.
    assert "Mode: UPDATE" in bash(tmp_path, STUB_STEPS + HEALTHY_SERVER + "main").stdout


def test_is_initialized_reports_every_failed_check(tmp_path):
    res = bash(tmp_path, HEALTHY_SERVER + "st_backend() { return 1; }; is_initialized || echo \"failed: ${STATE_FAILURES[*]}\"")
    assert "failed:" in res.stdout
    assert "successful setup marker" in res.stdout and "backend container healthy" in res.stdout


def test_failure_reason_reaches_the_log_file(tmp_path):
    """The terminal output, including why a run failed, is also in the log (no colour codes)."""
    res = bash(tmp_path, """
        C_RED=$'\\e[31m'; C_OFF=$'\\e[0m'
        start_logging
        MODE=SETUP; COMPONENT=HTTPS
        ok "a check that passed"
        die "https://scat-search.ru answered HTTP 000" "curl: (28) Connection timed out"
    """, env={"SCAT_LOG_TEED": "0"})
    assert res.returncode == 1
    time.sleep(0.3)  # the tee process flushes after the shell exits
    log = (tmp_path / "deploy.log").read_text()
    for line in ("a check that passed", "FAILED: HTTPS", "answered HTTP 000", "Connection timed out",
                 "Marker: not written (SETUP failed)", "RESULT: SETUP FAILED at HTTPS"):
        assert line in log, line
    assert "\x1b[" not in log
