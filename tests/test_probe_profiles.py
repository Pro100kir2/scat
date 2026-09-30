"""Installer decisions: which devices get which binary, and which get nothing."""
import pytest

from app.probes.profiles import detect_profile, evaluate

SIZES = {p: 4096 for p in ("linux-amd64", "linux-386", "linux-arm64", "linux-armv7", "linux-armv6", "linux-armv5",
                           "linux-mips", "linux-mipsle", "linux-mips64", "linux-mips64le", "linux-riscv64")}


def linux(**overrides) -> dict:
    diag = {
        "os": "Linux", "kernel": "6.1.0-18-amd64", "machine": "x86_64", "elf_class": 64, "endian": "little",
        "cpu_model": "Intel(R) Celeron(R) N5105", "cpu_cores": 4,
        "mem_total_kb": 8_000_000, "mem_available_kb": 6_000_000,
        "storage_free_kb": 20_000_000, "storage_total_kb": 60_000_000,
        "init": "systemd", "systemd": True, "curl": True, "wget": True, "wget_https": True,
        "is_root": True, "ipv4": True, "ipv6": False,
    }
    diag.update(overrides)
    return diag


def openwrt_mt7621(**overrides) -> dict:
    """Xiaomi/Netgear-class router: MediaTek MT7621 (MIPS 1004Kc, little-endian), OpenWrt 23.05."""
    diag = linux(
        kernel="5.15.137", machine="mips", elf_class=32, endian="little",
        cpu_model="MediaTek MT7621 ver:1 eco:3", cpu_cores=4,
        mem_total_kb=249_000, mem_available_kb=180_000,
        storage_free_kb=90_000, storage_total_kb=100_000,
        init="procd", systemd=False, curl=False, wget=True, wget_https=True,
        openwrt=True, openwrt_version="23.05.3", openwrt_arch="mipsel_24kc", openwrt_board="ramips/mt7621",
    )
    diag.update(overrides)
    return diag


def codes(verdict) -> set[str]:
    return {r["code"] for r in verdict.reasons}


# --- architectures -------------------------------------------------------------------
@pytest.mark.parametrize("diag, profile", [
    (dict(machine="x86_64", elf_class=64), "linux-amd64"),
    (dict(machine="x86_64", elf_class=32), "linux-386"),  # 64-bit kernel, 32-bit userland
    (dict(machine="i686"), "linux-386"),
    (dict(machine="aarch64", elf_class=64), "linux-arm64"),
    (dict(machine="aarch64", elf_class=32), "linux-armv7"),  # 32-bit Raspberry Pi OS on a 64-bit kernel
    (dict(machine="armv7l"), "linux-armv7"),
    (dict(machine="armv8l"), "linux-armv7"),
    (dict(machine="armv6l"), "linux-armv6"),
    (dict(machine="armv5tel"), "linux-armv5"),
    (dict(machine="mips", elf_class=32, endian="little"), "linux-mipsle"),
    (dict(machine="mips", elf_class=32, endian="big"), "linux-mips"),
    (dict(machine="mips", openwrt_arch="mipsel_24kc"), "linux-mipsle"),  # endianness from OpenWrt
    (dict(machine="mips", openwrt_arch="mips_24kc"), "linux-mips"),
    (dict(machine="mips64", elf_class=64, endian="big"), "linux-mips64"),
    (dict(machine="mips64", elf_class=32, endian="little"), "linux-mipsle"),  # 32-bit userland
    (dict(machine="riscv64"), "linux-riscv64"),
])
def test_profile_detection(diag, profile):
    assert detect_profile(diag)[0] == profile


@pytest.mark.parametrize("machine", ["sparc64", "ppc64le", "s390x", "armv8b", ""])
def test_unsupported_architecture(machine):
    verdict = evaluate(linux(machine=machine), SIZES)
    assert verdict.verdict == "UNSUPPORTED"
    assert codes(verdict) & {"arch_unsupported", "arch_big_endian_arm"}
    assert verdict.alternatives  # always tell the user what to do instead


def test_mips_without_byte_order_is_refused_not_guessed():
    verdict = evaluate(linux(machine="mips", elf_class=32, endian=""), SIZES)
    assert verdict.verdict == "UNSUPPORTED" and "arch_endian_unknown" in codes(verdict)


# --- whole-device verdicts --------------------------------------------------------------
def test_x86_64_server_is_supported():
    verdict = evaluate(linux(), SIZES)
    assert verdict.verdict == "SUPPORTED"
    assert (verdict.profile, verdict.service, verdict.install_dir) == ("linux-amd64", "systemd", "/usr/local/bin")
    assert not verdict.reasons


def test_raspberry_pi_arm_is_supported():
    verdict = evaluate(linux(machine="armv7l", elf_class=32, kernel="6.6.20+rpt-rpi-v7", cpu_model="ARMv7 Processor rev 4 (v7l)",
                             mem_total_kb=948_000, mem_available_kb=700_000), SIZES)
    assert verdict.verdict == "SUPPORTED" and verdict.profile == "linux-armv7"


def test_openwrt_mt7621_is_supported_with_procd():
    verdict = evaluate(openwrt_mt7621(), SIZES)
    assert verdict.verdict == "SUPPORTED", verdict.reasons
    assert verdict.profile == "linux-mipsle"
    assert verdict.service == "procd"
    assert verdict.install_dir == "/usr/bin"
    assert verdict.summary["openwrt_version"] == "23.05.3"
    assert verdict.summary["cpu_model"].startswith("MediaTek MT7621")


def test_insufficient_ram():
    verdict = evaluate(linux(mem_total_kb=30_000, mem_available_kb=10_000), SIZES)
    assert verdict.verdict == "UNSUPPORTED"
    assert {"ram_insufficient", "ram_available_insufficient"} <= codes(verdict)
    assert any(c.name == "mem_total" and not c.ok for c in verdict.checks)


def test_insufficient_storage_on_openwrt_suggests_extroot():
    verdict = evaluate(openwrt_mt7621(storage_free_kb=2_000), SIZES)
    assert verdict.verdict == "UNSUPPORTED" and "storage_insufficient" in codes(verdict)
    assert [a["code"] for a in verdict.alternatives] == ["openwrt_storage"]
    assert "extroot" in verdict.alternatives[0]["message"]


def test_storage_requirement_follows_the_real_binary_size():
    sizes = {**SIZES, "linux-amd64": 9_000}
    assert evaluate(linux(storage_free_kb=9_500), sizes).verdict == "UNSUPPORTED"
    assert evaluate(linux(storage_free_kb=10_100), sizes).verdict == "SUPPORTED"


@pytest.mark.parametrize("os_name", ["Darwin", "FreeBSD", "Windows_NT", ""])
def test_unsupported_os(os_name):
    verdict = evaluate(linux(os=os_name), SIZES)
    assert verdict.verdict == "UNSUPPORTED" and codes(verdict) == {"os_unsupported"}
    assert verdict.alternatives[0]["code"] == "other_device"
    assert "same ISP" in verdict.alternatives[0]["message"]


def test_old_kernel_no_root_no_https_no_ipv4():
    verdict = evaluate(linux(kernel="2.6.36", is_root=False, curl=False, wget_https=False, ipv4=False), SIZES)
    assert {"kernel_too_old", "not_root", "no_https_client", "no_ipv4"} <= codes(verdict)


def test_missing_build_on_server_is_unsupported():
    verdict = evaluate(linux(machine="riscv64"), {"linux-amd64": 4096})
    assert "binary_unavailable" in codes(verdict)


def test_no_init_system_still_installs_but_warns():
    verdict = evaluate(linux(init="unknown", systemd=False), SIZES)
    assert verdict.verdict == "SUPPORTED" and verdict.service == "none"
    assert any(w["code"] == "no_autostart" for w in verdict.warnings)


def test_garbage_input_never_raises():
    verdict = evaluate({"os": "Linux", "machine": None, "mem_total_kb": "lots", "elf_class": "x"}, SIZES)
    assert verdict.verdict == "UNSUPPORTED"
