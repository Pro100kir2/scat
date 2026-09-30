"""The network a probe really sits in: ASN, AS holder and prefix of its public IP.

Detected from the address the probe registered from (not from what the volunteer typed), with
RIPEstat's prefix-overview (public, no key, https; covers every RIR). Best effort: a failed
lookup leaves the probe working and is retried later by the maintenance loop.
"""
import asyncio
import ssl
from dataclasses import dataclass
from typing import Optional

import aiohttp
import certifi

from app import config
from app.probes.targets import is_public_ip

TIMEOUT = 8.0
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class AsnLookupError(Exception):
    """No ASN for this address (non-public, unannounced, or the lookup service failed)."""


@dataclass(frozen=True)
class Network:
    asn: int
    name: Optional[str]
    prefix: Optional[str]


def _parse(data: dict) -> Network:
    payload = data.get("data") or {}
    asns = payload.get("asns") or []
    if not asns or not isinstance(asns[0], dict) or not str(asns[0].get("asn", "")).isdigit():
        raise AsnLookupError("address is not announced by any AS")
    first = asns[0]
    asn = int(first["asn"])
    if not 1 <= asn <= 4_294_967_295:
        raise AsnLookupError("invalid ASN in reply")
    name = str(first.get("holder") or "")[:120] or None
    prefix = str(payload.get("resource") or "")[:64] or None
    return Network(asn, name, prefix)


async def lookup(ip: str) -> Network:
    if not ip or not is_public_ip(ip):
        raise AsnLookupError("not a public address")
    template = config.ASN_LOOKUP_URL
    if not template:
        raise AsnLookupError("ASN lookup disabled")
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False, connector=connector) as session:
            async with session.get(template.format(ip=ip), headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    raise AsnLookupError(f"lookup service answered HTTP {response.status}")
                return _parse(await response.json(content_type=None))
    except AsnLookupError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise AsnLookupError(f"lookup failed: {type(exc).__name__}") from None
