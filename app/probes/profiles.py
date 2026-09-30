"""Device diagnostics → SUPPORTED/UNSUPPORTED and the matching installation profile.

The installer collects facts on the device and sends them here before downloading
anything. All decisions live on the server so they can be tested and changed without
touching devices, and so an unsupported device never gets a binary.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

# Minimums for the Go agent: ~6-10 MB RSS at runtime, static binary of a few MB.
MIN_MEM_TOTAL_KB = 48 * 1024
MIN_MEM_AVAILABLE_KB = 16 * 1024
STORAGE_MARGIN_KB = 1024
DEFAULT_BINARY_KB = 8 * 1024  # used when the manifest does not know the size (MIPS builds are ~6.9 MB)
MIN_KERNEL = (3, 2)  # Go runtime requirement on Linux

# profile → (GOOS/GOARCH/variant description). The dist directory has one binary per profile.
PROFILES: dict[str, str] = {
    "linux-amd64": "Linux x86_64",
    "linux-386": "Linux x86 (32-bit)",
    "linux-arm64": "Linux ARM64 (aarch64)",
    "linux-armv7": "Linux ARMv7 (armhf)",
    "linux-armv6": "Linux ARMv6",
    "linux-armv5": "Linux ARMv5",
    "linux-mips": "Linux MIPS big-endian (softfloat)",
    "linux-mipsle": "Linux MIPS little-endian (softfloat)",
    "linux-mips64": "Linux MIPS64 big-endian",
    "linux-mips64le": "Linux MIPS64 little-endian",
    "linux-riscv64": "Linux RISC-V 64",
}

SERVICES = ("systemd", "procd", "openrc", "sysvinit")

# Alternatives carry a code (the website shows its own wording) and an English message
# (what the terminal installer prints).
ALTERNATIVE_OTHER_DEVICE = {
    "code": "other_device",
    "message": "Reachability depends on the network, not the device: install the probe on any other "
               "Linux device behind the same ISP connection (a router with OpenWrt, a Raspberry Pi, "
               "a PC or a virtual machine).",
}


@dataclass
class Check:
    name: str
    ok: bool
    value: str
    required: str


@dataclass
class Verdict:
    verdict: str  # SUPPORTED | UNSUPPORTED
    profile: Optional[str] = None
    service: Optional[str] = None
    install_dir: Optional[str] = None
    reasons: list[dict] = field(default_factory=list)  # failed requirements: {code, message}
    warnings: list[dict] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)  # {code, message}
    checks: list[Check] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def supported(self) -> bool:
        return self.verdict == "SUPPORTED"

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "profile": self.profile,
            "profile_name": PROFILES.get(self.profile or ""),
            "service": self.service,
            "install_dir": self.install_dir,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "alternatives": self.alternatives,
            "checks": [c.__dict__ for c in self.checks],
            "summary": self.summary,
        }


def _int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes"):
        return True
    if text in ("0", "false", "no"):
        return False
    return None


def _kernel_tuple(release: str) -> Optional[tuple[int, int]]:
    match = re.match(r"^(\d+)\.(\d+)", release or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _mb(kb: Optional[int]) -> str:
    return "unknown" if kb is None else f"{kb / 1024:.0f} MB"


def detect_profile(diag: dict) -> tuple[Optional[str], Optional[dict]]:
    """Map uname/ELF/OpenWrt facts to a binary profile, or (None, reason)."""
    machine = str(diag.get("machine") or "").strip().lower()
    elf_class = _int(diag.get("elf_class"))  # 32 / 64: the userland, not the kernel
    endian = str(diag.get("endian") or "").strip().lower()  # little / big
    owrt_arch = str(diag.get("openwrt_arch") or "").strip().lower()  # e.g. mipsel_24kc

    if owrt_arch.startswith("mipsel_") or owrt_arch.startswith("mips64el_"):
        endian = endian or "little"
    elif owrt_arch.startswith("mips_") or owrt_arch.startswith("mips64_"):
        endian = endian or "big"

    if machine in ("x86_64", "amd64"):
        return ("linux-386" if elf_class == 32 else "linux-amd64"), None
    if re.fullmatch(r"i[3-6]86", machine) or machine == "x86":
        return "linux-386", None
    if machine in ("aarch64", "arm64", "armv8b", "aarch64_be"):
        if machine in ("armv8b", "aarch64_be"):
            return None, {"code": "arch_big_endian_arm", "message": f"big-endian ARM ({machine}) is not supported"}
        # A 64-bit kernel with a 32-bit userland (e.g. 32-bit Raspberry Pi OS) needs the ARMv7 build.
        return ("linux-armv7" if elf_class == 32 else "linux-arm64"), None
    if machine.startswith("armv8") or machine.startswith("armv7"):
        return "linux-armv7", None
    if machine.startswith("armv6"):
        return "linux-armv6", None
    if machine.startswith("armv5") or machine.startswith("armv4t"):
        return "linux-armv5", None
    if machine.startswith("mips"):
        # The userland decides: OpenWrt on a 64-bit MIPS SoC may still run 32-bit binaries.
        is64 = elf_class == 64 if elf_class in (32, 64) else "64" in machine
        if endian not in ("little", "big"):
            if machine.endswith("el") or machine in ("mipsel", "mips64el"):
                endian = "little"
            else:
                return None, {"code": "arch_endian_unknown",
                              "message": "cannot determine MIPS byte order (big/little endian)"}
        base = "linux-mips64" if is64 else "linux-mips"
        return (base + "le" if endian == "little" else base), None
    if machine == "riscv64":
        return "linux-riscv64", None
    return None, {"code": "arch_unsupported", "message": f"CPU architecture {machine or 'unknown'!r} is not supported"}


def evaluate(diag: dict, binary_sizes_kb: Optional[dict[str, int]] = None) -> Verdict:
    """Decide whether the device can run a probe. Never raises on odd input."""
    sizes = binary_sizes_kb or {}
    reasons: list[dict] = []
    warnings: list[dict] = []
    checks: list[Check] = []
    alternatives: list[dict] = []

    os_name = str(diag.get("os") or "").strip()
    is_linux = os_name.lower() == "linux"
    is_openwrt = bool(_bool(diag.get("openwrt")))
    kernel = str(diag.get("kernel") or "")

    summary = {
        "os": os_name or "unknown",
        "distro": str(diag.get("distro") or "")[:80],
        "distro_version": str(diag.get("distro_version") or "")[:40],
        "kernel": kernel[:80],
        "machine": str(diag.get("machine") or "")[:32],
        "cpu_model": str(diag.get("cpu_model") or "")[:120],
        "cpu_cores": _int(diag.get("cpu_cores")),
        "mem_total_kb": _int(diag.get("mem_total_kb")),
        "mem_available_kb": _int(diag.get("mem_available_kb")),
        "storage_free_kb": _int(diag.get("storage_free_kb")),
        "storage_total_kb": _int(diag.get("storage_total_kb")),
        "init": str(diag.get("init") or "")[:20],
        "openwrt": is_openwrt,
        "openwrt_version": str(diag.get("openwrt_version") or "")[:40],
        "openwrt_arch": str(diag.get("openwrt_arch") or "")[:40],
        "is_root": _bool(diag.get("is_root")),
        "ipv4": _bool(diag.get("ipv4")),
        "ipv6": _bool(diag.get("ipv6")),
        "docker": _bool(diag.get("docker")),
    }

    # 1. Operating system and kernel.
    checks.append(Check("os", is_linux, os_name or "unknown", "Linux"))
    if not is_linux:
        reasons.append({"code": "os_unsupported", "message": f"operating system {os_name or 'unknown'!r} is not supported, Linux is required"})
        alternatives.append(dict(ALTERNATIVE_OTHER_DEVICE))
        return Verdict("UNSUPPORTED", reasons=reasons, alternatives=alternatives, checks=checks, summary=summary)

    kv = _kernel_tuple(kernel)
    kernel_ok = kv is None or kv >= MIN_KERNEL
    checks.append(Check("kernel", kernel_ok, kernel or "unknown", f">= {MIN_KERNEL[0]}.{MIN_KERNEL[1]}"))
    if not kernel_ok:
        reasons.append({"code": "kernel_too_old", "message": f"Linux kernel {kernel} is too old, {MIN_KERNEL[0]}.{MIN_KERNEL[1]} or newer is required"})

    # 2. Architecture → profile.
    profile, arch_reason = detect_profile(diag)
    checks.append(Check("arch", profile is not None, summary["machine"] or "unknown", "x86_64, x86, ARM64, ARMv5-v7, MIPS, MIPS64, RISC-V 64"))
    if arch_reason:
        reasons.append(arch_reason)
    elif sizes and profile not in sizes:
        reasons.append({"code": "binary_unavailable", "message": f"no probe build for {profile} is published on this server"})

    # 3. Memory.
    total, available = summary["mem_total_kb"], summary["mem_available_kb"]
    mem_ok = total is None or total >= MIN_MEM_TOTAL_KB
    checks.append(Check("mem_total", mem_ok, _mb(total), f">= {_mb(MIN_MEM_TOTAL_KB)}"))
    if not mem_ok:
        reasons.append({"code": "ram_insufficient", "message": f"{_mb(total)} of RAM, at least {_mb(MIN_MEM_TOTAL_KB)} is required"})
    avail_ok = available is None or available >= MIN_MEM_AVAILABLE_KB
    checks.append(Check("mem_available", avail_ok, _mb(available), f">= {_mb(MIN_MEM_AVAILABLE_KB)}"))
    if not avail_ok:
        reasons.append({"code": "ram_available_insufficient", "message": f"only {_mb(available)} of RAM is free, at least {_mb(MIN_MEM_AVAILABLE_KB)} is required"})
    if total is None:
        warnings.append({"code": "ram_unknown", "message": "could not read the amount of RAM"})

    # 4. Storage for the binary.
    need_kb = sizes.get(profile or "", DEFAULT_BINARY_KB) + STORAGE_MARGIN_KB
    free = summary["storage_free_kb"]
    storage_ok = free is not None and free >= need_kb
    checks.append(Check("storage_free", storage_ok, _mb(free), f">= {_mb(need_kb)}"))
    if not storage_ok:
        reasons.append({"code": "storage_insufficient", "message": f"{_mb(free)} of free storage, at least {_mb(need_kb)} is required"})
        if is_openwrt:
            alternatives.append({"code": "openwrt_storage",
                                 "message": "OpenWrt: free space on /overlay (remove unused packages) or set up extroot on a USB drive."})

    # 5. HTTPS client for the download.
    https = bool(_bool(diag.get("curl"))) or bool(_bool(diag.get("wget_https")))
    checks.append(Check("https_client", https, "curl" if _bool(diag.get("curl")) else ("wget" if _bool(diag.get("wget_https")) else "none"), "curl or wget with HTTPS"))
    if not https:
        hint = " (opkg install uclient-fetch libustream-mbedtls)" if is_openwrt else " (install curl)"
        reasons.append({"code": "no_https_client", "message": "no HTTPS-capable curl or wget found" + hint})

    # 6. Privileges: the probe is installed as a system service.
    root = _bool(diag.get("is_root"))
    checks.append(Check("root", root is True, "yes" if root else "no", "root"))
    if root is not True:
        reasons.append({"code": "not_root", "message": "root privileges are required to install a system service; re-run with sudo"})

    # 7. Connectivity: the checks run over IPv4.
    ipv4 = _bool(diag.get("ipv4"))
    checks.append(Check("ipv4", ipv4 is True, "yes" if ipv4 else "no", "outbound IPv4"))
    if ipv4 is not True:
        reasons.append({"code": "no_ipv4", "message": "no outbound IPv4 connectivity"})

    # 8. Init system → service type.
    init = summary["init"].lower()
    service = init if init in SERVICES else None
    if is_openwrt and init in ("", "procd"):
        service = "procd"
    checks.append(Check("init", True, init or "unknown", "systemd, procd, OpenRC or SysV init"))
    if service is None:
        warnings.append({"code": "no_autostart", "message": "no supported init system: the probe will run, but it will not start again after a reboot"})

    if summary["docker"] and reasons:
        alternatives.append({"code": "docker_host",
                             "message": "Docker is available on this device, but the probe needs direct access to the host network; install on the host itself."})
    if reasons and not alternatives and any(r["code"].startswith(("arch", "ram", "kernel", "binary")) for r in reasons):
        alternatives.append(dict(ALTERNATIVE_OTHER_DEVICE))

    install_dir = "/usr/bin" if is_openwrt else "/usr/local/bin"
    if reasons:
        return Verdict("UNSUPPORTED", profile=profile, service=service, install_dir=install_dir, reasons=reasons,
                       warnings=warnings, alternatives=alternatives, checks=checks, summary=summary)
    return Verdict("SUPPORTED", profile=profile, service=service or "none", install_dir=install_dir,
                   warnings=warnings, checks=checks, summary=summary)
