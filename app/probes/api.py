"""HTTP API for provider probes: the website side (/api/providers) and the agent side (/api/probes).

Agents only ever make outbound HTTPS requests to these endpoints; nothing connects to them.
Every body has a strict schema, and jobs can only take the shapes defined in targets.py.
"""
import re
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app import config
from app.api.rate_limit import RateLimiter, client_ip
from app.probes import profiles, service as service_module
from app.probes.phone import InvalidPhone, normalize_phone
from app.probes.releases import manifest
from app.probes.service import EnrollmentError, ProbeAuthError, ProbeForbidden

router = APIRouter()

MAX_BODY_BYTES = 64 * 1024
enroll_limiter = RateLimiter(config.PROBE_ENROLLMENTS_PER_HOUR, 3600, "Too many provider requests, try again later")
public_limiter = RateLimiter(60, 60, "Too many requests")
probe_limiter = RateLimiter(config.PROBE_REQUESTS_PER_MINUTE, 60, "Probe request rate exceeded")
auth_fail_limiter = RateLimiter(30, 60, "Too many failed authentications")

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URLISH_RE = re.compile(r"(https?:|://|www\.|<|>)", re.IGNORECASE)
_SAFE_ENV_RE = re.compile(r"[^\x20-\x7e]|['\"`$\\]")


def svc():
    return service_module.probe_service


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _clean_text(value: str, *, allow_urls: bool = False) -> str:
    value = re.sub(r"\s+", " ", _CONTROL_RE.sub(" ", value or "")).strip()
    if not allow_urls and _URLISH_RE.search(value):
        raise ValueError("links and markup are not allowed")
    return value


def api_base(request: Request) -> str:
    """Public origin for install commands and download URLs."""
    if config.PUBLIC_API_URL:
        return config.PUBLIC_API_URL
    host = request.url.hostname or "localhost"
    scheme = "http" if host in ("localhost", "127.0.0.1", "::1") else "https"
    port = request.url.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    return f"{scheme}://{netloc}"


# ====================================================================== schemas
Short = Annotated[str, StringConstraints(max_length=128)]


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=48)
    country: str = Field(pattern=r"^[A-Za-z]{2}$")
    region: str = Field(default="", max_length=64)
    asn: Optional[str] = Field(default=None, max_length=16)
    info: str = Field(default="", max_length=500)
    # Optional, for the admin only: never shown on the site or returned by any public endpoint.
    contact_phone: Optional[str] = Field(default=None, max_length=32)

    @field_validator("contact_phone")
    @classmethod
    def _phone(cls, v: Optional[str]) -> Optional[str]:
        try:
            return normalize_phone(v)
        except InvalidPhone as exc:
            raise ValueError(str(exc)) from None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean_text(v)
        if len(v) < 2:
            raise ValueError("name is too short")
        return v

    @field_validator("region")
    @classmethod
    def _region(cls, v: str) -> str:
        return _clean_text(v)

    @field_validator("info")
    @classmethod
    def _info(cls, v: str) -> str:
        return _clean_text(v, allow_urls=False)

    @field_validator("asn")
    @classmethod
    def _asn(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not str(v).strip():
            return None
        match = re.fullmatch(r"(?i)\s*(?:AS)?\s*(\d{1,10})\s*", str(v))
        if not match or not 1 <= int(match.group(1)) <= 4_294_967_295:
            raise ValueError("ASN must look like AS8359 or 8359")
        return match.group(1)


class EnrollmentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_id: str = Field(max_length=32)
    token: str = Field(max_length=128)


class Diagnostics(BaseModel):
    """Facts the installer collects. Unknown keys are dropped, unknown values are null."""
    model_config = ConfigDict(extra="ignore")
    os: Optional[Short] = None
    distro: Optional[Short] = None
    distro_version: Optional[Short] = None
    kernel: Optional[Short] = None
    machine: Optional[Short] = None
    cpu_model: Optional[Short] = None
    cpu_cores: Optional[int] = Field(default=None, ge=0, le=4096)
    mem_total_kb: Optional[int] = Field(default=None, ge=0)
    mem_available_kb: Optional[int] = Field(default=None, ge=0)
    storage_total_kb: Optional[int] = Field(default=None, ge=0)
    storage_free_kb: Optional[int] = Field(default=None, ge=0)
    storage_path: Optional[Short] = None
    init: Optional[Short] = None
    systemd: Optional[bool] = None
    openwrt: Optional[bool] = None
    openwrt_version: Optional[Short] = None
    openwrt_arch: Optional[Short] = None
    openwrt_board: Optional[Short] = None
    curl: Optional[bool] = None
    wget: Optional[bool] = None
    wget_https: Optional[bool] = None
    busybox: Optional[bool] = None
    docker: Optional[bool] = None
    is_root: Optional[bool] = None
    user: Optional[Short] = None
    elf_class: Optional[int] = Field(default=None, ge=0, le=64)
    endian: Optional[Short] = None
    ipv4: Optional[bool] = None
    ipv6: Optional[bool] = None
    installer_version: Optional[Short] = None


class DiagnosticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Optional[str] = Field(default=None, max_length=128)
    diagnostics: Diagnostics


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(max_length=128)
    agent_version: str = Field(pattern=r"^\d+\.\d+\.\d+([-+.][0-9A-Za-z.-]{1,20})?$")
    profile: str = Field(max_length=32)
    platform: str = Field(pattern=r"^[a-z0-9_]{1,16}/[a-z0-9_]{1,16}$")

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        if v not in profiles.PROFILES:
            raise ValueError("unknown profile")
        return v


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_version: str = Field(pattern=r"^\d+\.\d+\.\d+([-+.][0-9A-Za-z.-]{1,20})?$")
    uptime_seconds: int = Field(default=0, ge=0)
    jobs_running: int = Field(default=0, ge=0, le=64)


class ResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=service_module.JOB_ID_RE.pattern)
    outcome: Literal["SUCCESS", "FAILED", "TIMEOUT", "ERROR"]
    latency_ms: Optional[float] = Field(default=None, ge=0, le=60_000)
    error: Optional[str] = Field(default=None, pattern=r"^[a-z0-9_]{1,40}$")
    http_status: Optional[int] = Field(default=None, ge=100, le=599)
    tls_version: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9. ]{1,16}$")


class ResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[ResultItem] = Field(max_length=50)


# ====================================================================== auth
def current_probe(request: Request, authorization: str = Header(default="")) -> dict:
    try:
        probe = svc().authenticate(authorization)
    except ProbeAuthError:
        auth_fail_limiter.check(client_ip(request))
        raise HTTPException(status_code=401, detail="Invalid probe credentials",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except ProbeForbidden:
        raise HTTPException(status_code=403, detail="This probe is disabled") from None
    probe_limiter.check(probe["id"])
    return probe


# ====================================================================== website
@router.get("/providers")
async def list_providers(response: Response):
    """Providers with active probes, and the probe dashboard. Read-only; never starts checks."""
    _no_store(response)
    return svc().providers_overview()


@router.post("/providers", status_code=201)
async def add_provider(body: EnrollmentRequest, request: Request, response: Response):
    """'Add your provider': creates (or joins) the provider and a one-time install token."""
    _no_store(response)
    enroll_limiter.check(client_ip(request))
    result = svc().create_enrollment(
        name=body.name, country=body.country.upper(), region=body.region,
        asn=int(body.asn) if body.asn else None, info=body.info, client_ip=client_ip(request),
        contact_phone=body.contact_phone,
    )
    base = api_base(request)
    script = f"{base}/api/probes/install.sh"
    token = result["token"]
    result["install"] = {
        "script_url": script,
        "curl": f"curl -fsSL {script} | sh -s -- --token {token}",
        "wget": f"wget -qO- {script} | sh -s -- --token {token}",
        "diagnose_only": f"curl -fsSL {script} | sh -s -- --token {token} --diagnose-only",
        "releases_url": f"{base}/api/probes/releases",
    }
    return result


@router.post("/providers/enrollment")
async def enrollment_status(body: EnrollmentStatusRequest, request: Request, response: Response):
    """Installation progress for the website: waiting → installing/unsupported → registered → online."""
    _no_store(response)
    public_limiter.check(client_ip(request))
    try:
        return svc().enrollment_status(body.enrollment_id, body.token)
    except EnrollmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


# ====================================================================== installer & agent
def _env_line(key: str, value) -> str:
    text = "" if value is None else str(value)
    return f"{key}={_SAFE_ENV_RE.sub('', text)[:300]}"


def _as_env(result: dict, base: str) -> str:
    """Line-based reply for the POSIX installer: it never evals, only matches known keys."""
    lines = [_env_line("verdict", result["verdict"]), _env_line("profile", result["profile"] or ""),
             _env_line("profile_name", result.get("profile_name") or ""),
             _env_line("service", result.get("service") or ""), _env_line("install_dir", result.get("install_dir") or "")]
    release = result.get("release")
    if release:
        lines += [_env_line("version", release["version"]), _env_line("sha256", release["sha256"]),
                  _env_line("size", release["size"]),
                  _env_line("url", f"{base}/api/probes/download/{release['profile']}")]
    lines += [_env_line("reason", f"{r['code']}: {r['message']}") for r in result["reasons"]]
    lines += [_env_line("warning", w["message"]) for w in result["warnings"]]
    lines += [_env_line("alternative", a["message"]) for a in result["alternatives"]]
    return "\n".join(lines) + "\n"


@router.post("/probes/diagnostics")
async def diagnostics(body: DiagnosticsRequest, request: Request, response: Response,
                      format: Literal["json", "env"] = Query(default="json")):
    """Step one of every installation. Answers SUPPORTED (with the profile and checksum) or
    UNSUPPORTED (with the failed requirements). Nothing is downloaded before this."""
    _no_store(response)
    public_limiter.check(client_ip(request))
    try:
        result = svc().diagnose(body.diagnostics.model_dump(), token=body.token)
    except EnrollmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if format == "env":
        return PlainTextResponse(_as_env(result, api_base(request)), headers={"Cache-Control": "no-store"})
    return result


@router.post("/probes/register", status_code=201)
async def register(body: RegisterRequest, request: Request, response: Response):
    _no_store(response)
    enroll_limiter.check(client_ip(request))
    try:
        return svc().register(body.token, agent_version=body.agent_version, profile=body.profile,
                              platform=body.platform, public_ip=client_ip(request))
    except EnrollmentError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


@router.post("/probes/heartbeat")
async def heartbeat(body: HeartbeatRequest, request: Request, response: Response, probe: dict = Depends(current_probe)):
    _no_store(response)
    return await svc().heartbeat(probe, agent_version=body.agent_version, public_ip=client_ip(request))


@router.get("/probes/jobs")
async def jobs(response: Response, probe: dict = Depends(current_probe),
               wait: int = Query(default=0, ge=0, le=25), limit: int = Query(default=10, ge=1, le=50)):
    """Long-poll for work. Each job is one typed network check; there is no other kind."""
    _no_store(response)
    return {"jobs": await svc().lease_jobs(probe, wait_seconds=wait, limit=limit)}


@router.post("/probes/results")
async def results(body: ResultsRequest, response: Response, probe: dict = Depends(current_probe)):
    _no_store(response)
    reply = svc().submit_results(probe, [r.model_dump() for r in body.results])
    await svc().flush_activations()  # a probe that just passed verification starts working at once
    return reply


@router.get("/probes/verify/{nonce}", status_code=204, include_in_schema=False)
async def verify(nonce: str, request: Request):
    """The one-time URL of a verification or spot-check job. The probe's request arriving
    here is the proof that it executes jobs; the answer is always an empty 204 so the
    URL reveals nothing, whether the nonce is valid or not."""
    public_limiter.check(client_ip(request))
    svc().nonce_hit(nonce, client_ip(request))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/probes/rotate")
async def rotate(response: Response, probe: dict = Depends(current_probe)):
    """New secret; the old one keeps working for an hour so a crash mid-rotation is harmless."""
    _no_store(response)
    return svc().rotate(probe)


@router.get("/probes/releases")
async def releases(request: Request):
    m = manifest(config.PROBE_DIST_DIR)
    base = api_base(request)
    return {
        "version": m.version,
        "releases": [
            {"profile": r.profile, "name": profiles.PROFILES[r.profile], "sha256": r.sha256, "size": r.size,
             "url": f"{base}/api/probes/download/{r.profile}"}
            for r in sorted(m.releases.values(), key=lambda r: r.profile)
        ],
    }


@router.get("/probes/download/{profile}")
async def download(profile: str):
    release = manifest(config.PROBE_DIST_DIR).get(profile)
    if release is None:
        raise HTTPException(status_code=404, detail="No such probe build")
    return FileResponse(release.path, media_type="application/octet-stream",
                        filename=f"scat-probe-{release.profile}",
                        headers={"Cache-Control": "public, max-age=3600", "X-Checksum-Sha256": release.sha256})


@router.get("/probes/install.sh", response_class=PlainTextResponse)
async def install_script(request: Request):
    try:
        script = config.PROBE_INSTALLER.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="Installer not available") from None
    base = api_base(request)
    if not re.fullmatch(r"https?://[A-Za-z0-9.:\[\]-]+", base):
        raise HTTPException(status_code=500, detail="Invalid PUBLIC_API_URL")
    return PlainTextResponse(script.replace("__SCAT_API_URL__", base), media_type="text/x-shellscript",
                             headers={"Cache-Control": "no-cache"})
