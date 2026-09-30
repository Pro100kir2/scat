"""Share-link compatibility checks that do NOT trust Skat's own parser.

`link_matches_tested_config()` proves  node.link -> parse_vless -> build_outbound  equals the
tested outbound. Serializer and parser share one normalisation, so a mistake made in both
passes that check. Here every link is decoded by tests/refclient.py, an independent
implementation of the XTLS share-link standard, and compared with what the scanner tests.

Two questions are kept apart:
  * user link fidelity:   tested outbound == reference client(node.link)
  * source fidelity:      tested outbound == reference client(node.source_link)
The first must always hold (it is what users import). The second can legitimately differ
only where the scanner deliberately drops something it cannot test.
"""
import base64
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app import config
from app.services.scanner import build_outbound, parse_vless
from tests import refclient

UUID = "11111111-2222-3333-4444-555555555555"
PBK = "EYfpgja4VjpLUnWHtDJQ1XZk6xL5j4rXwf80Sw6keV0"

# The five nodes Skat showed on 2026-09-24 (scanner 3.0), verbatim from data/scan_all.json.
FIVE = {
    "gb-grpc-plain": "vless://cd3bb7d9-7df3-4644-ac05-c260990ac277@66.90.104.42:2083?type=grpc&serviceName=vless"
                     "#\U0001F1EC\U0001F1E7[openproxylist.com",
    "de-ws-cloudflare": "vless://a148f4c1-f95f-4c03-9768-e5d80b6b4996@188.114.97.6:80?path=%2FCooonfigCooonfig"
                        "CooonfigCooonfigCooonfig&security=none&type=ws&encryption=none&host=msjsi.azazilvpn.sbs."
                        "#\U0001F310سرور",
    "de-reality": "vless://4bdeee92-97e8-414d-bef6-ec1d5e2ab73b@gerwai1488.vvzzkontaktezzvv.garden:443?encryption=none"
                  "&flow=xtls-rprx-vision&type=tcp&security=reality&sni=gerwai1488.vvzzkontaktezzvv.garden"
                  "&pbk=EYfpgja4VjpLUnWHtDJQ1XZk6xL5j4rXwf80Sw6keV0&sid=0c13e8822e7f58aa&fp=firefox&spx=/"
                  "#%5BOpenRay%5D%20%F0%9F%87%A9%F0%9F%87%AA%20DE-24542",
    "lt-reality": "vless://c5694dc5-39fd-4a92-8430-3837baa522a3@lt.aether-runet.top:443?security=reality&type=tcp"
                  "&packetEncoding=xudp&sni=lt.aether-runet.top&fp=firefox&flow=xtls-rprx-vision&sid=4fa44664f6a566d3"
                  "&pbk=vrcsm8L_tY7sNbnCEdLCEjOthh_w9bdWS3wFPgsIomk#\U0001F1F1\U0001F1F9[openproxylist.com",
    "gb-reality": "vless://c5694dc5-39fd-4a92-8430-3837baa522a3@31.76.39.141:443?security=reality&encryption=none"
                  "&pbk=G2U8_BvRAgcOw0sX8u_0yYdVAlD8CWLSi-uYjvH07hw&headerType=none&fp=firefox&type=tcp"
                  "&flow=xtls-rprx-vision&sni=gb.superbuba.top&sid=4fa44664f6a566d3#\U0001F1EC\U0001F1E7[openproxylist.com",
}

SHAPES = {
    "reality-full": f"vless://{UUID}@r.example.com:443?type=tcp&security=reality&sni=www.microsoft.com&fp=firefox"
                    f"&pbk={PBK}&sid=6ba85179e30d4fc2&spx=%2F%3Fq%3D1%26x%3D%23&flow=xtls-rprx-vision#DE",
    "reality-empty-sid": f"vless://{UUID}@r.example.com:443?type=tcp&security=reality&sni=www.microsoft.com&pbk={PBK}&sid=",
    "reality-no-sid-no-fp-no-spx": f"vless://{UUID}@r.example.com:443?type=tcp&security=reality&sni=a.example&pbk={PBK}",
    "reality-aliases": f"vless://{UUID}@r.example.com:443?network=raw&security=reality&serverName=a.example"
                       f"&publicKey={PBK}&shortId=ab&fingerprint=safari&spiderX=%2Fx",
    "grpc-tls": f"vless://{UUID}@g.example.com:443?type=grpc&security=tls&sni=g.example.com&serviceName=a%2Fb%20c",
    "grpc-multi": f"vless://{UUID}@g.example.com:443?type=grpc&security=tls&sni=g.example.com&serviceName=s&mode=multi",
    "grpc-plain": f"vless://{UUID}@1.2.3.4:2083?type=grpc&serviceName=vless",
    "ws-plain": f"vless://{UUID}@1.2.3.4:80?type=ws&path=%2Fws%3Fed%3D2048&host=cdn.example.com.",
    "ws-no-path": f"vless://{UUID}@1.2.3.4:80?type=ws&host=cdn.example.com",
    "ws-tls-alpn": f"vless://{UUID}@w.example.com:443?type=ws&security=tls&sni=w.example.com&alpn=h2%2Chttp%2F1.1&path=%2F",
    "ipv6": f"vless://{UUID}@[2001:db8::1]:8443?type=tcp&security=tls&sni=v6.example.com&fp=chrome",
}

ALL = {**FIVE, **SHAPES}


def _tested(node) -> dict:
    return refclient.canonical(build_outbound(node, "t"))


def client(link: str, **kw) -> dict:
    return refclient.canonical(refclient.outbound(link, **kw))


def flat(d, prefix=""):
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            out.update(flat(v, f"{prefix}.{k}"))
        return out
    if isinstance(d, list):
        out = {}
        for i, v in enumerate(d):
            out.update(flat(v, f"{prefix}[{i}]"))
        return out
    return {prefix: d}


def explain(a: dict, b: dict) -> list[str]:
    fa, fb = flat(a), flat(b)
    return [f"{k}: tested={fa.get(k, '<absent>')!r} client={fb.get(k, '<absent>')!r}"
            for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]


# --- 1. what users import is what was tested (independent decoder) --------------------

@pytest.mark.parametrize("name", ALL)
def test_user_link_decodes_to_the_tested_outbound_in_a_reference_client(name):
    node = parse_vless(ALL[name])
    assert explain(_tested(node), client(node.link)) == []


@pytest.mark.parametrize("name", ALL)
def test_user_link_survives_form_decoding_clients(name):
    """Clients using application/x-www-form-urlencoded decoding turn '+' into a space."""
    node = parse_vless(ALL[name])
    assert "+" not in urlsplit(node.link).query
    assert explain(_tested(node), client(node.link, plus_as_space=True)) == []


# --- 2. the tested outbound is what the source link means ------------------------------

@pytest.mark.parametrize("name", FIVE)
def test_the_five_reported_nodes_were_tested_exactly_as_their_source_links_describe(name):
    node = parse_vless(FIVE[name])
    assert explain(_tested(node), client(FIVE[name])) == []


@pytest.mark.parametrize(
    "link",
    [
        pytest.param(f"vless://{UUID}@h.example:80?type=ws&path=/a+b&host=h.example#n", id="plus-in-path"),
        pytest.param(f"vless://{UUID}@h.example:80?type=grpc&serviceName=a+b#n", id="plus-in-serviceName"),
        pytest.param(f"vless://{UUID}@h.example:80?type=ws&path=/x)", id="trailing-paren-without-fragment"),
        pytest.param(f"vless://{UUID}@h.example:80?type=ws&host=cdn.example.", id="trailing-dot-without-fragment"),
    ],
)
@pytest.mark.xfail(strict=True, reason="known: parse_qs form-decodes '+', clean_vless_candidate strips trailing chars")
def test_scanner_tests_what_the_source_link_says(link):
    assert explain(_tested(parse_vless(link)), client(link)) == []


# --- 3. REALITY fields land in the right places ----------------------------------------

@pytest.mark.parametrize("name", ["de-reality", "lt-reality", "gb-reality"])
def test_reality_fields_are_not_swapped_or_altered(name):
    src = refclient.parse(FIVE[name])["q"]
    node = parse_vless(FIVE[name])
    link = refclient.parse(node.link)["q"]
    reality = build_outbound(node, "t")["streamSettings"]["realitySettings"]
    for key, field in (("pbk", "publicKey"), ("sni", "serverName"), ("fp", "fingerprint"), ("sid", "shortId")):
        assert src[key] == link[key] == reality[field], key
    key = reality["publicKey"]
    assert len(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))) == 32  # an X25519 public key
    assert re.fullmatch(r"(?:[0-9a-f]{2}){0,8}", reality["shortId"])
    assert src["flow"] == link["flow"] == build_outbound(node, "t")["settings"]["vnext"][0]["users"][0]["flow"]
    assert src.get("spx", "") == link.get("spx", "") == reality.get("spiderX", "")


# --- 4. gRPC: which fields exist where --------------------------------------------------

def test_grpc_plain_node_fields():
    node = parse_vless(FIVE["gb-grpc-plain"])
    stream = build_outbound(node, "t")["streamSettings"]
    assert stream == {"network": "grpc", "security": "none", "grpcSettings": {"serviceName": "vless"}}
    q = refclient.parse(node.link)["q"]
    assert q["serviceName"] == "vless" and q["security"] == "none"
    assert not {"sni", "fp", "mode", "authority", "host"} & set(q)


@pytest.mark.parametrize("name", [n for n in ALL if "security=tls" in ALL[n]])
@pytest.mark.xfail(strict=True, reason="known: a TLS link without fp is tested with Xray's default hello, which "
                   "depends on the Xray version; the link should state fp explicitly")
def test_tls_fingerprint_is_explicit(name):
    node = parse_vless(ALL[name].replace("&fp=chrome", ""))
    assert build_outbound(node, "t")["streamSettings"]["tlsSettings"].get("fingerprint")
    assert refclient.parse(node.link)["q"].get("fp")


# --- 5. strict URI syntax that picky clients (java.net.URI, Swift URL) accept -----------

UNRESERVED_OR_PCT = r"(?:[A-Za-z0-9\-._~]|%[0-9A-F]{2})*"


@pytest.mark.parametrize("name", ALL)
def test_user_link_is_strict_rfc3986(name):
    link = parse_vless(ALL[name]).link
    m = re.fullmatch(r"vless://([^@]+)@(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.\-]+):(\d+)\?([^#]*)(?:#(.*))?", link)
    assert m, link
    pairs = m.group(4).split("&")
    assert all(pairs) and not m.group(4).endswith("&")
    for pair in pairs:
        key, eq, value = pair.partition("=")
        assert eq and value, pair  # no empty or valueless parameters
        assert re.fullmatch(UNRESERVED_OR_PCT, value), pair
    assert len({p.split("=")[0] for p in pairs}) == len(pairs)  # no duplicate keys
    if m.group(5) is not None:
        assert re.fullmatch(UNRESERVED_OR_PCT, m.group(5))


# --- 6. Xray itself accepts the reference-client config built from the user link --------

XRAY = next((p for p in (config.XRAY_BINARY, shutil.which("xray") or "") if p and Path(p).exists()), None)


@pytest.mark.skipif(XRAY is None, reason="xray binary not installed")
@pytest.mark.parametrize("name", ALL)
def test_xray_accepts_the_reference_client_config(name, tmp_path):
    ob = dict(refclient.outbound(parse_vless(ALL[name]).link), tag="proxy")
    cfg = {"inbounds": [{"listen": "127.0.0.1", "port": 1, "protocol": "socks"}], "outbounds": [ob]}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cfg))
    run = subprocess.run([XRAY, "run", "-test", "-c", str(path)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
