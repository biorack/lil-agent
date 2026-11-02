"""CBorg (https://api.cborg.lbl.gov): key, model list, quick probes."""

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.cborg.lbl.gov"
HOST = "api.cborg.lbl.gov"
DEFAULT_MODEL = "lbl/cborg-coder-max"
KEY_FILE = Path.home() / "secrets" / "cborg-api-key.txt"

# Proxy variants of the same models; hidden from the default model list.
ALIAS_SUFFIXES = ("-compact", "-compressed", "-flex")

# Off LBLnet, CBorg only accepts IP addresses authorized on its key page, and a
# laptop can have an authorized IPv6 address but an unauthorized IPv4 one. Try
# IPv6 first (as RFC 6724 recommends) so every connection makes the same choice.
_getaddrinfo = socket.getaddrinfo


def _ipv6_first(*args, **kwargs):
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda a: a[0] != socket.AF_INET6)


socket.getaddrinfo = _ipv6_first

IP_HELP = ("CBorg refused this computer's IP address. Off LBLnet, connect via the LBL VPN, "
           "or authorize your addresses (IPv4 and IPv6) at https://api.cborg.lbl.gov/key/manage")


def api_key():
    if os.environ.get("CBORG_API_KEY"):
        return os.environ["CBORG_API_KEY"]
    path = Path(os.environ.get("CBORG_API_KEY_FILE", KEY_FILE)).expanduser()
    if path.exists():
        return path.read_text().strip()
    sys.exit(f"lil-agent: set CBORG_API_KEY or put the key in {KEY_FILE}")


def call(path, body=None, timeout=300):
    headers = {"Authorization": f"Bearer {api_key()}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 403 and b"not recognized" in e.read():
            sys.exit(f"lil-agent: {IP_HELP}")
        raise


def family_status():
    """HTTP status of GET /v1/models over IPv6 and over IPv4 (None: no route)."""
    status = {}
    for name, family in (("IPv6", socket.AF_INET6), ("IPv4", socket.AF_INET)):
        try:
            addr = _getaddrinfo(HOST, 443, family, socket.SOCK_STREAM)[0][4]
            with socket.create_connection(addr[:2], timeout=10) as raw, \
                 ssl.create_default_context().wrap_socket(raw, server_hostname=HOST) as tls:
                tls.sendall(f"GET /v1/models HTTP/1.1\r\nHost: {HOST}\r\n"
                            f"Authorization: Bearer {api_key()}\r\n"
                            f"Connection: close\r\n\r\n".encode())
                status[name] = int(tls.recv(64).split()[1])
        except (OSError, IndexError, ValueError):
            status[name] = None
    return status


def _public(d):
    """Drop anything credential-like before a model entry is saved or shown."""
    secret = ("api_key", "secret", "auth", "header", "credential", "password")
    return {k: v for k, v in (d or {}).items()
            if not any(s in k.lower() for s in secret)}


def model_entry(name):
    """The /model/info entry for one model, without credentials, or None."""
    for entry in call("/model/info").get("data", []):
        if entry["model_name"] == name:
            return {"model_name": name,
                    "model_info": _public(entry.get("model_info")),
                    "litellm_params": _public(entry.get("litellm_params"))}
    return None


def model_rows(include_all=False):
    rows = []
    for entry in call("/model/info").get("data", []):
        name = entry["model_name"]
        if not include_all and (not name.startswith("lbl/")
                                or name.endswith(ALIAS_SUFFIXES)):
            continue
        info = entry.get("model_info") or {}
        lp = entry.get("litellm_params") or {}
        cost_in = info.get("input_cost_per_token")
        cost_out = info.get("output_cost_per_token")
        rows.append({
            "name": name,
            "mode": info.get("mode") or "",
            "backend": lp.get("model") or "",
            "api_base": (lp.get("api_base") or "").split("//")[-1].split("/")[0],
            "ctx": info.get("max_input_tokens") or info.get("max_tokens") or "",
            "usd_per_M_in_out": ("" if cost_in is None else
                                 f"{cost_in * 1e6:g}/{(cost_out or 0) * 1e6:g}"),
            "tools": {True: "yes", False: "no"}.get(
                info.get("supports_function_calling"), "?"),
        })
    return sorted(rows, key=lambda r: r["name"])


def print_table(rows):
    cols = ["name", "mode", "backend", "api_base", "ctx",
            "usd_per_M_in_out", "tools"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


TOOL = {
    "type": "function",
    "function": {
        "name": "match_neutral_loss",
        "description": ("Look up which common neutral losses match a mass "
                        "difference (Da) between two ions."),
        "parameters": {
            "type": "object",
            "properties": {"mass_difference": {"type": "number"}},
            "required": ["mass_difference"],
        },
    },
}


def chat(model, messages, **extra):
    t0 = time.time()
    reply = call("/v1/chat/completions",
                 {"model": model, "messages": messages,
                  "cache": {"no-cache": True}, **extra})
    return reply, time.time() - t0


def probe(model):
    """One-word reply and one tool call; prints results, returns True if both work."""
    print(f"== {model}")
    try:
        reply, dt = chat(model, [{"role": "user", "content":
                                  "Reply with 'OK'. No other commentary."}])
        text = (reply["choices"][0]["message"].get("content") or "").strip()
        print(f"  ping: {text[:40]!r} in {dt:.1f} s")

        reply, dt = chat(model, [{"role": "user", "content":
                                  "Which neutral loss is 162.0528 Da? Use the tool."}],
                         tools=[TOOL], tool_choice="auto")
        msg = reply["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            print(f"  tool call: NONE (answered in text: "
                  f"{(msg.get('content') or '').strip()[:80]!r})")
            return False
        fn = calls[0]["function"]
        args = json.loads(fn.get("arguments") or "{}")
        ok = (fn["name"] == "match_neutral_loss"
              and abs(float(args.get("mass_difference", 0)) - 162.0528) < 1e-3)
        print(f"  tool call: {'PASS' if ok else 'WRONG'} "
              f"{fn['name']}({json.dumps(args)}) in {dt:.1f} s")
        return ok
    except urllib.error.HTTPError as e:
        print(f"  ERROR HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
    except Exception as e:  # report and let the caller move on
        print(f"  ERROR {type(e).__name__}: {e}")
    return False
