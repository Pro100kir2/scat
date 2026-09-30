import time
from collections import deque

from fastapi import HTTPException, Request

from app import config

MAX_TRACKED_CLIENTS = 10_000


class RateLimiter:
    """Small in-memory sliding-window limiter, enough for one API process."""

    def __init__(self, limit: int, window_seconds: float = 60.0, detail: str = "Too many scan requests"):
        self.limit = limit
        self.window = window_seconds
        self.detail = detail
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            retry_after = int(self.window - (now - hits[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=self.detail,
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
        if len(self._hits) > MAX_TRACKED_CLIENTS:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] <= self.window}


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


scan_limiter = RateLimiter(config.SCAN_RATE_LIMIT_PER_MINUTE)
