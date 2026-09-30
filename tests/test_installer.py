"""The POSIX installer, run for real against fake devices and a fake API.

Each test builds a device root (SCAT_PROBE_ROOT: /proc, /etc, ELF header of /bin/sh...),
stubs uname/id/df/systemctl on PATH, and runs probe/install.sh with /bin/sh.
"""
import hashlib
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.probes.profiles import evaluate

INSTALLER = Path(__file__).resolve().parent.parent / "probe" / "install.sh"
SHELL = shutil.which("dash") or "/bin/sh"
TOKEN = "skenr_TestToken-123_abc"

DEVICES = {
    "openwrt_mt7621": dict(
        uname={"-s": "Linux", "-r": "5.15.137", "-m": "mips"}, elf=(1, 1),
        files={
            "etc/openwrt_release": "DISTRIB_ID='OpenWrt'\nDISTRIB_RELEASE='23.05.3'\nDISTRIB_ARCH='mipsel_24kc'\n"
                                   "DISTRIB_TARGET='ramips/mt7621'\nDISTRIB_DESCRIPTION='OpenWrt 23.05.3 r23809'\n",
            "proc/cpuinfo": "system type\t\t: MediaTek MT7621 ver:1 eco:3\nmachine\t\t\t: Xiaomi Mi Router 4A Gigabit\n"
                            "processor\t\t: 0\ncpu model\t\t: MIPS 1004Kc V2.15\nprocessor\t\t: 1\n"
                            "processor\t\t: 2\nprocessor\t\t: 3\n",
            "proc/meminfo": "MemTotal:         249072 kB\nMemFree:          150000 kB\nMemAvailable:     182000 kB\n",
        },
        dirs=["usr/bin", "sbin", "etc/init.d"], df=(12_000, 9_000),
    ),
    "debian_x86_64": dict(
        uname={"-s": "Linux", "-r": "6.1.0-18-amd64", "-m": "x86_64"}, elf=(2, 1),
        files={
            "etc/os-release": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\nVERSION_ID="12"\n',
            "proc/cpuinfo": "processor\t: 0\nmodel name\t: Intel(R) Celeron(R) N5105 @ 2.00GHz\nprocessor\t: 1\n",
            "proc/meminfo": "MemTotal:        8000000 kB\nMemAvailable:    6000000 kB\n",
        },
        dirs=["usr/local/bin", "run/systemd/system", "etc/init.d"], df=(60_000_000, 20_000_000),
    ),
    "raspberry_pi_armv7": dict(
        uname={"-s": "Linux", "-r": "6.6.20+rpt-rpi-v7", "-m": "armv7l"}, elf=(1, 1),
        files={
            "etc/os-release": 'PRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"\nVERSION_ID="12"\n',
            "proc/cpuinfo": "processor\t: 0\nmodel name\t: ARMv7 Processor rev 4 (v7l)\nHardware\t: BCM2835\n",
            "proc/meminfo": "MemTotal:         948000 kB\nMemAvailable:     700000 kB\n",
        },
        dirs=["usr/local/bin", "run/systemd/system"], df=(30_000_000, 25_000_000),
    ),
    "pi_64bit_kernel_32bit_userland": dict(
        uname={"-s": "Linux", "-r": "6.6.20+rpt-rpi-v8", "-m": "aarch64"}, elf=(1, 1),
        files={"proc/meminfo": "MemTotal: 948000 kB\nMemAvailable: 700000 kB\n", "proc/cpuinfo": "processor : 0\n"},
        dirs=["usr/local/bin", "run/systemd/system"], df=(30_000_000, 25_000_000),
    ),
    "openwrt_ath79_mips_be": dict(
        uname={"-s": "Linux", "-r": "5.15.150", "-m": "mips"}, elf=(1, 2),
        files={
            "etc/openwrt_release": "DISTRIB_RELEASE='23.05.2'\nDISTRIB_ARCH='mips_24kc'\nDISTRIB_TARGET='ath79/generic'\n",
            "proc/cpuinfo": "system type\t\t: Qualcomm Atheros QCA9563 ver 1 rev 0\nprocessor\t\t: 0\n",
            "proc/meminfo": "MemTotal: 122000 kB\nMemAvailable: 70000 kB\n",
        },
        dirs=["usr/bin", "sbin"], df=(7_000, 3_000),  # 16 MB flash: too little room
    ),
    "old_kernel_no_memavailable": dict(
        uname={"-s": "Linux", "-r": "3.10.108", "-m": "armv7l"}, elf=(1, 1),
        files={"proc/meminfo": "MemTotal: 250000 kB\nMemFree: 20000 kB\nBuffers: 5000 kB\nCached: 30000 kB\n",
               "proc/cpuinfo": 'processor : 0\nmodel name : ARM "Cortex" A7 \\ quirky\n'},
        dirs=["usr/local/bin", "etc/init.d"], df=(4_000_000, 1_000_000),
    ),
}


def build_device(tmp_path: Path, name: str, uid: int = 0) -> tuple[Path, dict]:
    spec = DEVICES[name]
    root = tmp_path / "root"
    for rel, text in spec["files"].items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for rel in spec["dirs"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(parents=True, exist_ok=True)
    elf_class, endian = spec["elf"]
    (root / "bin" / "sh").write_bytes(b"\x7fELF" + bytes([elf_class, endian]) + b"\x01" + b"\0" * 57)
    (root / "proc" / "net").mkdir(parents=True, exist_ok=True)
    (root / "proc" / "net" / "route").write_text(
        "Iface\tDestination\tGateway\tFlags\nwan\t00000000\t0101A8C0\t0003\n")
    (root / "proc" / "net" / "ipv6_route").write_text("")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "commands.log"
    uname_cases = "\n".join(f'  {flag}) echo "{value}" ;;' for flag, value in spec["uname"].items())
    total, free = spec["df"]
    scripts = {
        "uname": f'#!/bin/sh\ncase "$1" in\n{uname_cases}\n  *) echo Linux ;;\nesac\n',
        "id": f'#!/bin/sh\ncase "$1" in -u) echo {uid} ;; -un) echo {"root" if uid == 0 else "user"} ;; esac\n',
        "df": f'#!/bin/sh\necho "Filesystem 1024-blocks Used Available Capacity Mounted"\n'
              f'echo "/dev/root {total} {total - free} {free} 50% /"\n',
        "systemctl": f'#!/bin/sh\necho "systemctl $*" >> "{log}"\n',
        "curl": "#!/bin/sh\nexit 0\n",  # present, but tests pass --api to a local server
        "sleep": "#!/bin/sh\nexit 0\n",
    }
    for cmd, body in scripts.items():
        path = stubs / cmd
        path.write_text(body)
        path.chmod(0o755)
    # Real HTTP for the fake API goes through the real curl.
    (stubs / "curl").unlink()
    env = {"PATH": f"{stubs}:{os.environ['PATH']}", "SCAT_PROBE_ROOT": str(root), "TMPDIR": str(tmp_path),
           "HOME": str(tmp_path), "LC_ALL": "C"}
    return root, env


def run_installer(env, *args, timeout=60):
    return subprocess.run([SHELL, str(INSTALLER), *args], env=env, capture_output=True, text=True, timeout=timeout)


def diagnostics(tmp_path, device):
    _, env = build_device(tmp_path, device)
    proc = run_installer(env, "--print-diagnostics")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --- DIAGNOSTIC: what the script collects on different devices ------------------------------
def test_openwrt_mt7621_diagnostics(tmp_path):
    d = diagnostics(tmp_path, "openwrt_mt7621")
    assert d["os"] == "Linux" and d["machine"] == "mips"
    assert (d["elf_class"], d["endian"]) == (32, "little")
    assert d["openwrt"] is True and d["openwrt_version"] == "23.05.3" and d["openwrt_arch"] == "mipsel_24kc"
    assert d["openwrt_board"] == "ramips/mt7621"
    assert d["cpu_model"] == "MediaTek MT7621 ver:1 eco:3" and d["cpu_cores"] == 4
    assert (d["mem_total_kb"], d["mem_available_kb"]) == (249072, 182000)
    assert (d["storage_total_kb"], d["storage_free_kb"], d["storage_path"]) == (12000, 9000, "/usr/bin")
    assert d["init"] == "procd" and d["systemd"] is False
    assert d["is_root"] is True and d["ipv4"] is True and d["ipv6"] is False
    verdict = evaluate(d, {"linux-mipsle": 5000})
    assert verdict.verdict == "SUPPORTED" and verdict.profile == "linux-mipsle" and verdict.service == "procd"


@pytest.mark.parametrize("device, profile", [
    ("debian_x86_64", "linux-amd64"),
    ("raspberry_pi_armv7", "linux-armv7"),
    ("pi_64bit_kernel_32bit_userland", "linux-armv7"),
    ("openwrt_ath79_mips_be", "linux-mips"),
])
def test_architecture_reaches_the_right_profile(tmp_path, device, profile):
    d = diagnostics(tmp_path, device)
    assert evaluate(d, {})  # never raises
    from app.probes.profiles import detect_profile
    assert detect_profile(d)[0] == profile


def test_x86_64_systemd_details(tmp_path):
    d = diagnostics(tmp_path, "debian_x86_64")
    assert d["distro"] == "Debian GNU/Linux 12 (bookworm)" and d["distro_version"] == "12"
    assert d["init"] == "systemd" and d["systemd"] is True and d["storage_path"] == "/usr/local/bin"
    assert d["cpu_model"].startswith("Intel(R) Celeron(R) N5105") and d["cpu_cores"] == 2


def test_small_flash_router_is_unsupported_for_storage(tmp_path):
    d = diagnostics(tmp_path, "openwrt_ath79_mips_be")
    verdict = evaluate(d, {"linux-mips": 5000})
    assert verdict.verdict == "UNSUPPORTED"
    assert {r["code"] for r in verdict.reasons} == {"storage_insufficient"}


def test_old_kernel_memory_fallback_and_json_escaping(tmp_path):
    d = diagnostics(tmp_path, "old_kernel_no_memavailable")
    assert d["mem_available_kb"] == 55000  # MemFree + Buffers + Cached
    assert d["cpu_model"] == 'ARM "Cortex" A7 \\ quirky'  # quotes and backslashes survive JSON
    assert d["init"] == "sysvinit"


def test_not_root_is_reported(tmp_path):
    _, env = build_device(tmp_path, "debian_x86_64")
    env["PATH"] = env["PATH"]
    (Path(env["PATH"].split(":")[0]) / "id").write_text('#!/bin/sh\ncase "$1" in -u) echo 1000 ;; -un) echo pi ;; esac\n')
    d = json.loads(run_installer(env, "--print-diagnostics").stdout.strip().splitlines()[-1])
    assert d["is_root"] is False and d["user"] == "pi"


# --- VERDICT and INSTALL against a fake API ---------------------------------------------------
FAKE_PROBE = """#!/bin/sh
case "$1" in
  version) echo "scat-probe 1.0.0 test" ;;
  enroll) shift; while [ $# -gt 0 ]; do case "$1" in --config) cfg="$2"; shift 2 ;; *) shift ;; esac; done
          mkdir -p "$(dirname "$cfg")"; echo '{"probe_id":"prb_0123456789abcdef"}' > "$cfg" ;;
  check) echo "probe prb_0123456789abcdef online" ;;
  run) exit 0 ;;
esac
"""


class FakeAPI:
    def __init__(self, reply_lines=None, binary: bytes = FAKE_PROBE.encode(), sha: str = None):
        self.requests: list[tuple[str, str, bytes]] = []
        self.binary = binary
        self.sha = sha
        self.reply_lines = reply_lines
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                api.requests.append(("POST", self.path, body))
                text = "\n".join(api.reply()) + "\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(text.encode())

            def do_GET(self):
                api.requests.append(("GET", self.path, b""))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(api.binary)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reply(self):
        if self.reply_lines is not None:
            return [line.replace("{API}", self.url) for line in self.reply_lines]
        sha = self.sha or hashlib.sha256(self.binary).hexdigest()
        return ["verdict=SUPPORTED", "profile=linux-amd64", "profile_name=Linux x86_64", "service=systemd",
                "install_dir=/usr/local/bin", "version=1.0.0", f"sha256={sha}", f"size={len(self.binary)}",
                f"url={self.url}/api/probes/download/linux-amd64"]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def api():
    servers = []

    def factory(**kwargs):
        server = FakeAPI(**kwargs)
        servers.append(server)
        return server

    yield factory
    for s in servers:
        s.close()


def test_unsupported_device_installs_nothing(tmp_path, api):
    root, env = build_device(tmp_path, "debian_x86_64")
    fake = api(reply_lines=["verdict=UNSUPPORTED", "profile=", "reason=ram_insufficient: 30 MB of RAM, at least 48 MB is required",
                            "alternative=Install the probe on another Linux device behind the same ISP."])
    proc = run_installer(env, "--api", fake.url, "--token", TOKEN)
    assert proc.returncode == 3
    assert "UNSUPPORTED" in proc.stdout and "30 MB of RAM" in proc.stdout and "another Linux device" in proc.stdout
    assert [r[0] for r in fake.requests] == ["POST"]  # the diagnostics only: nothing downloaded
    sent = json.loads(fake.requests[0][2])
    assert sent["token"] == TOKEN and sent["diagnostics"]["machine"] == "x86_64"
    assert not (root / "usr/local/bin/scat-probe").exists() and not (root / "etc/scat-probe").exists()


def test_diagnose_only_never_downloads(tmp_path, api):
    root, env = build_device(tmp_path, "debian_x86_64")
    fake = api()
    proc = run_installer(env, "--api", fake.url, "--diagnose-only")
    assert proc.returncode == 0 and "SUPPORTED: Linux x86_64" in proc.stdout
    assert [r[0] for r in fake.requests] == ["POST"]
    assert not (root / "usr/local/bin/scat-probe").exists()


def test_supported_device_full_installation(tmp_path, api):
    root, env = build_device(tmp_path, "debian_x86_64")
    fake = api()
    proc = run_installer(env, "--api", fake.url, "--token", TOKEN)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "checksum OK" in proc.stdout and "Online" in proc.stdout
    assert [r[0] for r in fake.requests] == ["POST", "GET"]  # diagnostics strictly before download
    binary = root / "usr/local/bin/scat-probe"
    assert binary.read_text() == FAKE_PROBE and os.access(binary, os.X_OK)
    assert json.loads((root / "etc/scat-probe/probe.json").read_text())["probe_id"].startswith("prb_")
    unit = (root / "etc/systemd/system/scat-probe.service").read_text()
    assert "ExecStart=/usr/local/bin/scat-probe run --config /etc/scat-probe/probe.json" in unit
    assert "CapabilityBoundingSet=\n" in unit and "NoNewPrivileges=yes" in unit
    assert "systemctl enable --now scat-probe" in (tmp_path / "commands.log").read_text()


def test_checksum_mismatch_aborts_before_installing(tmp_path, api):
    root, env = build_device(tmp_path, "debian_x86_64")
    fake = api(sha="0" * 64)
    proc = run_installer(env, "--api", fake.url, "--token", TOKEN)
    assert proc.returncode != 0 and "checksum mismatch" in proc.stderr
    assert not (root / "usr/local/bin/scat-probe").exists()
    assert not (tmp_path / "commands.log").exists()


@pytest.mark.parametrize("bad_line, message", [
    ("url=http://evil.example/payload", "unexpected download URL"),
    ("install_dir=/etc", "unexpected install directory"),
    ("service=cron", "unknown service type"),
    ("profile=linux-amd64;reboot", "invalid profile"),
])
def test_malicious_server_replies_are_refused(tmp_path, api, bad_line, message):
    root, env = build_device(tmp_path, "debian_x86_64")
    fake = api()
    good = fake.reply()
    key = bad_line.split("=", 1)[0]
    fake.reply_lines = [line for line in good if not line.startswith(key + "=")] + [bad_line]
    proc = run_installer(env, "--api", fake.url, "--token", TOKEN)
    assert proc.returncode != 0 and message in proc.stderr
    assert [r[0] for r in fake.requests] == ["POST"]


def test_server_text_is_never_executed(tmp_path, api):
    root, env = build_device(tmp_path, "debian_x86_64")
    marker = tmp_path / "pwned"
    fake = api(reply_lines=["verdict=UNSUPPORTED", f"reason=$(touch {marker})`touch {marker}`",
                            f"alternative=; touch {marker}"])
    proc = run_installer(env, "--api", fake.url)
    assert proc.returncode == 3 and not marker.exists()


@pytest.mark.parametrize("args, message", [
    (["--token", "skenr_x;reboot"], "unexpected characters"),
    (["--token", "not-a-token"], "does not look like"),
    (["--api", "http://evil.example"], "invalid API address"),
    (["--frobnicate"], "unknown option"),
])
def test_bad_arguments(tmp_path, args, message):
    _, env = build_device(tmp_path, "debian_x86_64")
    proc = run_installer(env, *args)
    assert proc.returncode != 0 and message in proc.stderr


def test_uninstall_removes_everything(tmp_path, api):
    root, env = build_device(tmp_path, "debian_x86_64")
    fake = api()
    assert run_installer(env, "--api", fake.url, "--token", TOKEN).returncode == 0
    proc = run_installer(env, "--api", fake.url, "--uninstall")
    assert proc.returncode == 0
    for rel in ("usr/local/bin/scat-probe", "etc/scat-probe", "etc/systemd/system/scat-probe.service"):
        assert not (root / rel).exists()


def test_reinstall_keeps_the_existing_identity(tmp_path, api):
    root, env = build_device(tmp_path, "debian_x86_64")
    fake = api()
    assert run_installer(env, "--api", fake.url, "--token", TOKEN).returncode == 0
    (root / "etc/scat-probe/probe.json").write_text('{"probe_id":"prb_keepme00000000"}')
    proc = run_installer(env, "--api", fake.url, "--token", TOKEN)  # e.g. an upgrade
    assert proc.returncode == 0 and "Keeping the existing probe identity" in proc.stdout
    assert "prb_keepme" in (root / "etc/scat-probe/probe.json").read_text()
