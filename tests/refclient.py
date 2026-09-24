"""Independent reference VLESS share-link -> Xray outbound converter.

Written from the XTLS share-link standard (Xray-core discussion #716), NOT from Skat's
parser, so a bug shared by Skat's serializer and parser cannot hide here.

Semantics (per the standard / mainstream clients):
  * query values are percent-decoded (RFC 3986; '+' is NOT a space here, but see
    `plus_as_space` for clients that use form-decoding, e.g. Java URLDecoder)
  * type default tcp, security default none, encryption default none
  * tls/reality: sni default = remote host, fp default chrome
  * grpc: mode default gun, authority optional
  * ws: path default "/"
"""
from urllib.parse import unquote, unquote_plus


def split_query(query: str, plus_as_space: bool = False) -> dict:
    dec = unquote_plus if plus_as_space else unquote
    out = {}
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = dec(key)
        if key not in out:  # first occurrence wins
            out[key] = dec(value)
    return out


def parse(link: str, plus_as_space: bool = False) -> dict:
    assert link.startswith("vless://"), link
    rest = link[len("vless://"):]
    rest, _, fragment = rest.partition("#")
    rest, _, query = rest.partition("?")
    userinfo, _, hostport = rest.rpartition("@")
    hostport = hostport.rstrip("/")
    if hostport.startswith("["):
        host, _, port = hostport[1:].partition("]:")
    else:
        host, _, port = hostport.rpartition(":")
    q = split_query(query, plus_as_space)
    return {
        "uuid": unquote(userinfo),
        "host": host,
        "port": int(port),
        "name": unquote(fragment),
        "q": q,
    }


def outbound(link: str, plus_as_space: bool = False) -> dict:
    p = parse(link, plus_as_space)
    q = p["q"]
    network = (q.get("type") or "tcp").lower()
    security = (q.get("security") or "none").lower()
    user = {"id": p["uuid"], "encryption": q.get("encryption") or "none", "level": 0}
    if q.get("flow"):
        user["flow"] = q["flow"]
    stream = {"network": "raw" if network in ("tcp", "raw") else network, "security": security}

    if network == "ws":
        s = {"path": q.get("path") or "/"}
        if q.get("host"):
            s["host"] = q["host"]
        stream["wsSettings"] = s
    elif network == "grpc":
        s = {"serviceName": q.get("serviceName", "")}
        mode = q.get("mode") or "gun"
        if mode == "multi":
            s["multiMode"] = True
        if q.get("authority"):
            s["authority"] = q["authority"]
        stream["grpcSettings"] = s
    elif network in ("xhttp", "splithttp"):
        s = {"path": q.get("path") or "/"}
        if q.get("host"):
            s["host"] = q["host"]
        if q.get("mode"):
            s["mode"] = q["mode"]
        stream["xhttpSettings"] = s
    elif network == "httpupgrade":
        s = {"path": q.get("path") or "/"}
        if q.get("host"):
            s["host"] = q["host"]
        stream["httpupgradeSettings"] = s
    elif network in ("tcp", "raw") and q.get("headerType") == "http":
        stream["rawSettings"] = {"header": {"type": "http"}}

    if security in ("tls", "reality"):
        s = {"serverName": q.get("sni") or p["host"], "fingerprint": q.get("fp") or "chrome"}
        if security == "tls":
            if q.get("alpn"):
                s["alpn"] = q["alpn"].split(",")
            if q.get("allowInsecure") in ("1", "true"):
                s["allowInsecure"] = True
            stream["tlsSettings"] = s
        else:
            s["publicKey"] = q.get("pbk", "")
            s["shortId"] = q.get("sid", "")
            s["spiderX"] = q.get("spx", "")
            stream["realitySettings"] = s
    return {
        "protocol": "vless",
        "settings": {"vnext": [{"address": p["host"], "port": p["port"], "users": [user]}]},
        "streamSettings": stream,
    }


# Equivalences Xray itself applies, so the diff shows only behavioural differences.
def canonical(ob: dict) -> dict:
    import copy
    ob = copy.deepcopy(ob)
    ob.pop("tag", None)
    st = ob["streamSettings"]
    if st.get("network") == "tcp":
        st["network"] = "raw"
    ws = st.get("wsSettings")
    if ws is not None and not ws.get("path"):
        ws["path"] = "/"
    for key in ("httpupgradeSettings", "xhttpSettings"):
        if key in st and not st[key].get("path"):
            st[key]["path"] = "/"
    g = st.get("grpcSettings")
    if g is not None:
        g.setdefault("serviceName", "")
        if g.get("multiMode") is False:
            g.pop("multiMode")
    t = st.get("tlsSettings")
    if t is not None:
        # Current Xray docs: empty fingerprint means "chrome". Older cores used Go's own
        # ClientHello instead; test_tls_fingerprint_is_explicit covers that ambiguity.
        t["fingerprint"] = t.get("fingerprint") or "chrome"
    for key in ("realitySettings",):
        r = st.get(key)
        if r is not None:
            for f in ("shortId", "spiderX", "publicKey"):
                r.setdefault(f, "")
            r["spiderX"] = r["spiderX"] or "/"  # Xray: empty spiderX means "/"
    return ob
