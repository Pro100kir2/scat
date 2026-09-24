#!/usr/bin/env python3
"""Manual connectivity diagnosis for VLESS share links (not for CI: it needs the network).

Run it on the machine whose network you want to judge: the scanner host AND the
user's own network (laptop on the same Wi-Fi, phone hotspot, ...). Compare the outputs.

    python3 scripts/diagnose_links.py --cache data/scan_all.json          # links Skat showed
    python3 scripts/diagnose_links.py 'vless://...' 'vless://...'           # arbitrary links
    python3 scripts/diagnose_links.py --cache data/scan_all.json --json out.json

Needs only the Python stdlib, `curl` and an Xray binary (--xray, $XRAY_BINARY or ./xray).

For every link it tests up to three outbounds, each in its own Xray process:
  source   reference client (tests/refclient.py) built from the link as found in the source
  user     reference client built from the link Skat gives users (node.link / "original")
  scanner  Skat's own build_outbound() (only when the app package is importable)
and, before any tunnel, the raw network path:
  dns      A/AAAA of the server address
  tcp      plain TCP connect to address:port (IP/port blocking shows up here)
  hello    TLS ClientHello with the link's SNI straight to the server (SNI filtering /
           dead REALITY target shows up here; REALITY answers with the target's certificate)

Verdicts:
  URI_MISMATCH      user outbound != scanner outbound (a link bug; class A)
  NET_BLOCKED_TCP   TCP to the server fails from this network (class B)
  NET_BLOCKED_TLS   TCP works, TLS/REALITY handshake does not (class B, DPI/SNI)
  NET_STALLS        tunnel opens but bulk transfer stalls (class B, DPI throttling)
  WORKS             every probe passed from this network
"""
import argparse
import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests import refclient  # noqa: E402

try:  # optional: Skat's own view of the link
    from app.services import scanner as skat
except Exception:  # aiohttp etc. missing is fine
    skat = None

TRACE = "https://www.cloudflare.com/cdn-cgi/trace"
REACH = "https://www.gstatic.com/generate_204"
SPEED = "https://speed.cloudflare.com/__down?bytes={n}"


def sh(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception as exc:
        return f"<{type(exc).__name__}>"


def curl(url, socks_port=None, timeout=10, max_bytes=None):
    cmd = ["curl", "-sS", "-o", "-" if max_bytes is None else os.devnull, "-m", str(timeout),
           "-w", "\n%{http_code} %{size_download} %{time_total}"]
    if socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    run = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=timeout + 5)
    body, _, tail = run.stdout.rpartition("\n")
    parts = tail.split()
    code, size, secs = (int(parts[0]), int(parts[1]), float(parts[2])) if len(parts) == 3 else (0, 0, 0.0)
    return {"code": code, "bytes": size, "seconds": round(secs, 3), "body": body, "err": run.stderr.strip()[:200]}


def trace(socks_port=None, timeout=8):
    r = curl(TRACE, socks_port, timeout)
    data = dict(line.split("=", 1) for line in r["body"].splitlines() if "=" in line)
    return {"ok": r["code"] == 200 and data.get("h") == "www.cloudflare.com", "ip": data.get("ip"),
            "loc": data.get("loc"), "seconds": r["seconds"], "err": r["err"]}


def environment():
    env = {"platform": platform.platform(), "direct": trace(None, 10)}
    if sys.platform == "darwin":
        env["vpn_services"] = sh(["scutil", "--nc", "list"]).strip().splitlines()[1:]
        env["utun_with_ipv4"] = [ln for ln in sh(["ifconfig"]).split("\n\n") if ln.startswith("utun") and "inet " in ln]
        env["default_routes"] = [ln for ln in sh(["netstat", "-rn", "-f", "inet"]).splitlines() if ln.startswith("default")]
    else:
        env["routes"] = sh(["ip", "route"]).strip().splitlines()
        env["tun_links"] = [ln for ln in sh(["ip", "-o", "link"]).splitlines() if "tun" in ln or "wg" in ln]
    env["note"] = ("If a VPN/TUN is active or 'direct' is not your real ISP, every result below "
                   "describes the VPN's network, not yours.")
    return env


def resolve(host):
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except socket.gaierror as exc:
        return [f"<dns error {exc}>"]


def tcp_connect(ip, port, timeout=5):
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return {"ok": True, "ms": round((time.perf_counter() - start) * 1000)}
    except OSError as exc:
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:120]}


def cert_issuer(der):
    """Issuer of the presented certificate. A middlebox (corporate proxy, sandbox) that
    terminates TLS shows up here with its own CA, which makes the hello check meaningless."""
    try:
        fd, path = tempfile.mkstemp(suffix=".pem")
        os.write(fd, ssl.DER_cert_to_PEM_cert(der).encode())
        os.close(fd)
        info = ssl._ssl._test_decode_cert(path)  # stdlib helper; no third-party deps
        os.unlink(path)
        return ", ".join("=".join(x) for rdn in info.get("issuer", ()) for x in rdn)
    except Exception:
        return "?"


def tls_hello(ip, port, sni, timeout=6):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=sni) as tls:
                cert = tls.getpeercert(binary_form=True)
                return {"ok": True, "ms": round((time.perf_counter() - start) * 1000),
                        "version": tls.version(), "issuer": cert_issuer(cert)}
    except (OSError, ssl.SSLError) as exc:
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:160]}


def run_tunnel(xray, outbound, port):
    cfg = {"log": {"loglevel": "warning"},
           "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"udp": False}}],
           "outbounds": [dict(outbound, tag="proxy")]}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.write(fd, json.dumps(cfg).encode())
    os.close(fd)
    proc = subprocess.Popen([xray, "run", "-c", path], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    time.sleep(1.2)
    if proc.poll() is not None:
        return {"xray": "rejected config", "err": proc.stderr.read()[-300:]}
    try:
        out = {"trace": trace(port, 8)}
        if not out["trace"]["ok"]:
            return out  # nothing else can work; keep the run short
        out["reach"] = curl(REACH, port, 8)["code"] == 204
        for label, size, timeout in (("dl_64k", 64 * 1024, 10), ("dl_256k", 256 * 1024, 12), ("dl_2m", 2 * 1024 * 1024, 30)):
            r = curl(SPEED.format(n=size), port, timeout, max_bytes=size)
            out[label] = {"ok": r["code"] == 200 and r["bytes"] >= size, "bytes": r["bytes"], "seconds": r["seconds"],
                          "mbps": round(size * 8 / 1e6 / r["seconds"], 1) if r["seconds"] and r["bytes"] >= size else None}
        return out
    finally:
        proc.terminate()
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.unlink(path)


def canonical(ob):
    ob = refclient.canonical(ob)
    return ob


def diagnose(entry, xray, port, direct_ip):
    source, user = entry["source"], entry["user"]
    ref = refclient.parse(user)
    host, net_port, q = ref["host"], ref["port"], ref["q"]
    result = {"name": entry.get("name", ""), "source_link": source, "user_link": user, "address": host, "port": net_port,
              "network": q.get("type", "tcp"), "security": q.get("security", "none")}
    outbounds = {"source": refclient.outbound(source), "user": refclient.outbound(user)}
    if skat is not None:
        try:
            outbounds["scanner"] = skat.build_outbound(skat.parse_vless(source), "proxy")
        except Exception as exc:
            result["scanner_parse_error"] = str(exc)
    canon = {k: canonical(v) for k, v in outbounds.items()}
    result["uri_mismatch"] = [k for k in canon if canon[k] != canon["user"]]

    ips = resolve(host)
    result["dns"] = ips
    result["tcp"] = {ip: tcp_connect(ip, net_port) for ip in ips if not ip.startswith("<")}
    if result["security"] in ("tls", "reality"):
        sni = q.get("sni") or host
        result["hello"] = {ip: tls_hello(ip, net_port, sni) for ip in result["tcp"]}

    result["tunnels"] = {}
    for i, (label, ob) in enumerate(outbounds.items()):
        t = run_tunnel(xray, ob, port + i)
        if "trace" in t and t["trace"]["ok"] and direct_ip and t["trace"]["ip"] == direct_ip:
            t["trace"]["ok"] = False
            t["trace"]["err"] = "exit IP equals this machine's IP (not tunnelled)"
        result["tunnels"][label] = t

    u = result["tunnels"].get("user", {})
    if result["uri_mismatch"]:
        verdict = "URI_MISMATCH"
    elif result["tcp"] and not any(v["ok"] for v in result["tcp"].values()):
        verdict = "NET_BLOCKED_TCP"
    elif "hello" in result and not any(v["ok"] for v in result["hello"].values()):
        verdict = "NET_BLOCKED_TLS"
    elif not u.get("trace", {}).get("ok"):
        verdict = "TUNNEL_FAILS"
    elif not (u.get("dl_256k", {}).get("ok") and u.get("dl_2m", {}).get("ok")):
        verdict = "NET_STALLS"
    else:
        verdict = "WORKS"
    result["verdict"] = verdict
    return result


def load_cache(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for n in data.get("top", []):
        source = n.get("source_link") or n.get("original")
        user = n.get("link") or n.get("original")  # scanner 3.0 showed "original" to users
        out.append({"source": source, "user": user, "name": n.get("name", ""),
                    "scan": {k: n.get(k) for k in ("exit_ip", "exit_country", "latency_ms", "speed_mbps")}})
    return out, data.get("scanner_version")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links", nargs="*")
    ap.add_argument("--cache")
    ap.add_argument("--xray", default=os.getenv("XRAY_BINARY") or shutil.which("xray") or str(ROOT / "xray"))
    ap.add_argument("--port", type=int, default=31080)
    ap.add_argument("--json")
    args = ap.parse_args()

    entries, version = [], None
    if args.cache:
        entries, version = load_cache(args.cache)
    entries += [{"source": link, "user": skat.parse_vless(link).link if skat else link} for link in args.links]
    if not entries:
        ap.error("give links or --cache")
    if not Path(args.xray).exists():
        ap.error(f"xray binary not found: {args.xray}")

    env = environment()
    print(f"xray: {sh([args.xray, 'version']).splitlines()[0] if Path(args.xray).exists() else '?'}")
    print(f"direct exit: {env['direct'].get('ip')} ({env['direct'].get('loc')})   cache scanner_version: {version}")
    for key in ("vpn_services", "utun_with_ipv4", "default_routes", "tun_links"):
        if env.get(key):
            print(f"{key}: {env[key]}")
    report = {"environment": env, "scanner_version": version, "nodes": []}
    for i, entry in enumerate(entries, 1):
        r = diagnose(entry, args.xray, args.port, env["direct"].get("ip"))
        r["scan"] = entry.get("scan")
        report["nodes"].append(r)
        tun = r["tunnels"].get("user", {})
        tcp = "/".join("ok" if v["ok"] else "FAIL" for v in r["tcp"].values()) or "-"
        hello = "/".join("ok" if v["ok"] else "FAIL" for v in r.get("hello", {}).values()) or "-"
        print(f"#{i} {r['address']}:{r['port']} {r['network']}/{r['security']}  tcp={tcp} hello={hello} "
              f"trace={'ok' if tun.get('trace', {}).get('ok') else 'FAIL'} exit={tun.get('trace', {}).get('ip')} "
              f"256k={'ok' if tun.get('dl_256k', {}).get('ok') else 'FAIL'} "
              f"2m={tun.get('dl_2m', {}).get('mbps')} -> {r['verdict']}"
              + (f"  mismatch={r['uri_mismatch']}" if r["uri_mismatch"] else ""))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print(f"details: {args.json}")


if __name__ == "__main__":
    main()
