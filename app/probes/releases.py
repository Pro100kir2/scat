"""Prebuilt probe binaries shipped in the backend image, with their SHA-256 checksums.

Layout of PROBE_DIST_DIR (made by the Dockerfile's probe build stage):
    VERSION                      agent version, e.g. 1.0.0
    SHA256SUMS                   "<sha256>  scat-probe-<profile>" per line
    scat-probe-<profile>         one static binary per installation profile
"""
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.probes.profiles import PROFILES

_SUM_RE = re.compile(r"^([0-9a-f]{64})\s+\*?scat-probe-([a-z0-9-]+)$")


@dataclass(frozen=True)
class Release:
    profile: str
    path: Path
    sha256: str
    size: int

    @property
    def size_kb(self) -> int:
        return -(-self.size // 1024)


@dataclass(frozen=True)
class Manifest:
    version: str
    releases: dict[str, Release]

    def get(self, profile: Optional[str]) -> Optional[Release]:
        return self.releases.get(profile or "")

    def sizes_kb(self) -> dict[str, int]:
        return {p: r.size_kb for p, r in self.releases.items()}


def load_manifest(dist_dir: Path) -> Manifest:
    version_file, sums_file = dist_dir / "VERSION", dist_dir / "SHA256SUMS"
    version = version_file.read_text().strip() if version_file.is_file() else "unknown"
    releases: dict[str, Release] = {}
    if sums_file.is_file():
        for line in sums_file.read_text().splitlines():
            match = _SUM_RE.match(line.strip())
            if not match or match.group(2) not in PROFILES:
                continue
            path = dist_dir / f"scat-probe-{match.group(2)}"
            if path.is_file():
                releases[match.group(2)] = Release(match.group(2), path, match.group(1), path.stat().st_size)
    return Manifest(version, releases)


@lru_cache(maxsize=4)
def cached_manifest(dist_dir: str, mtime: float) -> Manifest:
    return load_manifest(Path(dist_dir))


def manifest(dist_dir: Path) -> Manifest:
    """Manifest re-read only when SHA256SUMS changes."""
    sums = dist_dir / "SHA256SUMS"
    mtime = sums.stat().st_mtime if sums.is_file() else 0.0
    return cached_manifest(str(dist_dir), mtime)
