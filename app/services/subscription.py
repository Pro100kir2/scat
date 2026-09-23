import base64
from typing import List, Dict, Any, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode, quote, unquote


def format_node_title(node: dict, rank: int) -> str:
    original_url = node.get("original", "")
    latency = node.get("latency_ms")
    latency_str = f"{latency:.0f}ms" if latency is not None else "OK"
    network = (node.get("network") or "tcp").upper()
    security = (node.get("security") or "none").upper()

    country_flag = "⚡"
    title_suffix = f"#{rank} {country_flag} [{latency_str}] {network}/{security}"

    node_name = node.get("name", "").strip()
    if node_name:
        clean_name = node_name.split("#")[0].strip()
        final_name = f"{title_suffix} - {clean_name}"
    else:
        address = node.get("address", "")
        port = node.get("port", "")
        final_name = f"{title_suffix} ({address}:{port})"

    return final_name


def customize_vless_url(original_url: str, new_name: str) -> str:
    try:
        parts = urlsplit(original_url)
        encoded_name = quote(new_name)
        new_parts = (parts.scheme, parts.netloc, parts.path, parts.query, encoded_name)
        return urlunsplit(new_parts)
    except Exception:
        return original_url


def generate_subscription_content(nodes: List[dict], format_type: str = "base64", rename: bool = True) -> str:
    urls = []
    for idx, node in enumerate(nodes, 1):
        orig_url = node.get("original", "")
        if not orig_url:
            continue
        if rename:
            new_title = format_node_title(node, idx)
            url = customize_vless_url(orig_url, new_title)
        else:
            url = orig_url
        urls.append(url)

    raw_text = "\n".join(urls) + ("\n" if urls else "")

    if format_type.lower() in ("base64", "b64"):
        return base64.b64encode(raw_text.encode("utf-8")).decode("utf-8")

    return raw_text
