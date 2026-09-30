"""Admin authentication: the ADMIN_TOKEN Bearer header (scripts, curl) or a browser session.

The admin panel never keeps ADMIN_TOKEN in JavaScript. It sends the token once to
POST /api/admin/session and gets a random session ID back in a cookie:

* HttpOnly (scripts on the page cannot read it), Secure (https only; plain http only for a
  local development API), SameSite=Strict (never sent by another site's requests),
  Path=/api/admin (never sent anywhere else);
* only its SHA-256 is stored; sessions expire after ADMIN_SESSION_TTL_SECONDS and after
  ADMIN_SESSION_IDLE_SECONDS without use; rotating ADMIN_TOKEN ends every session.

CSRF: SameSite=Strict already keeps the cookie off cross-site requests. In addition every
cookie-authenticated request must carry `X-Scat-Admin: 1` (a custom header forces a CORS
preflight, which only the exact ALLOWED_ORIGINS pass) and, when the browser sends one, an
Origin from ALLOWED_ORIGINS. Bearer requests carry no ambient credentials, so need neither.
"""
import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import HTTPException, Request, Response

from app import config, db, events
from app.api.rate_limit import RateLimiter, client_ip

COOKIE = "scat_admin"
COOKIE_PATH = "/api/admin"
CSRF_HEADER = "x-scat-admin"
TOUCH_EVERY = 60  # seconds between last_seen updates of a session

login_limiter = RateLimiter(10, 60, "Too many login attempts, try again in a minute")
failure_limiter = RateLimiter(30, 60, "Too many failed admin authentications")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _token_tag() -> str:
    """Identifies the current ADMIN_TOKEN without storing it: sessions from an old token die."""
    return _sha("scat-admin-session:" + config.ADMIN_TOKEN)[:16]


def token_matches(token: str) -> bool:
    return bool(config.ADMIN_TOKEN) and hmac.compare_digest(_sha(token or ""), _sha(config.ADMIN_TOKEN))


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    if config.TRUST_PROXY_HEADERS and request.headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    # Behind Caddy the API sees plain http; its public URL says what browsers use.
    return config.PUBLIC_API_URL.startswith("https://")


def create_session(request: Request, response: Response) -> dict:
    now = time.time()
    session_id = secrets.token_urlsafe(32)
    expires = now + config.ADMIN_SESSION_TTL_SECONDS
    db.store().run(
        "INSERT INTO admin_sessions (id_hash, token_tag, created_at, expires_at, last_seen, ip, user_agent)"
        " VALUES (?,?,?,?,?,?,?)",
        (_sha(session_id), _token_tag(), now, expires, now, client_ip(request),
         (request.headers.get("user-agent") or "")[:200]),
    )
    db.store().run("DELETE FROM admin_sessions WHERE expires_at < ? OR last_seen < ?",
                   (now, now - config.ADMIN_SESSION_IDLE_SECONDS))
    response.set_cookie(COOKIE, session_id, max_age=config.ADMIN_SESSION_TTL_SECONDS, path=COOKIE_PATH,
                        httponly=True, secure=_is_https(request), samesite="strict")
    return {"authenticated": True, "expires_at": expires, "idle_seconds": config.ADMIN_SESSION_IDLE_SECONDS}


def end_session(request: Request, response: Response) -> None:
    session_id = request.cookies.get(COOKIE)
    if session_id:
        db.store().run("DELETE FROM admin_sessions WHERE id_hash=?", (_sha(session_id),))
    response.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, secure=_is_https(request), samesite="strict")


def _session(request: Request) -> Optional[dict]:
    session_id = request.cookies.get(COOKIE)
    if not session_id or len(session_id) > 128:
        return None
    row = db.store().one("SELECT * FROM admin_sessions WHERE id_hash=?", (_sha(session_id),))
    now = time.time()
    if row is None or row["expires_at"] < now or now - row["last_seen"] > config.ADMIN_SESSION_IDLE_SECONDS \
            or not hmac.compare_digest(row["token_tag"], _token_tag()):
        return None
    if now - row["last_seen"] > TOUCH_EVERY:
        db.store().run("UPDATE admin_sessions SET last_seen=? WHERE id_hash=?", (now, row["id_hash"]))
    return row


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # same-origin navigations and non-browser clients send none
    return origin.rstrip("/") in config.ALLOWED_ORIGINS


def _fail(request: Request, reason: str) -> HTTPException:
    failure_limiter.check(client_ip(request))
    events.record("auth", "admin_auth_failed", f"Admin authentication failed: {reason}", severity="warning",
                  data={"ip": client_ip(request), "path": request.url.path})
    return HTTPException(status_code=401, detail="Admin authentication required",
                         headers={"WWW-Authenticate": "Bearer"})


def require_admin(request: Request) -> str:
    """FastAPI dependency for every /api/admin/* route. Returns how the caller authenticated."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=404)
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token_matches(token.strip()):
            return "token"
        raise _fail(request, "invalid bearer token")
    if request.cookies.get(COOKIE):
        if _session(request) is None:
            raise _fail(request, "expired or unknown session")
        if request.headers.get(CSRF_HEADER) != "1" or not _origin_allowed(request):
            raise HTTPException(status_code=403, detail="Missing admin request header or foreign origin")
        return "session"
    raise HTTPException(status_code=401, detail="Admin authentication required", headers={"WWW-Authenticate": "Bearer"})
