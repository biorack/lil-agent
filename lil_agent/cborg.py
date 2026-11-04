"""CBorg (https://api.cborg.lbl.gov): key, model list, quick probes."""

import difflib
import hashlib
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

# Set CBORG_BASE_URL if CBorg moves; the network guard follows it.
API_BASE = os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov").rstrip("/")
HOST = urllib.parse.urlsplit(API_BASE).hostname or API_BASE
PORT = urllib.parse.urlsplit(API_BASE).port or 443
DEFAULT_MODEL = "lbl/cborg-coder-max"
KEY_FILE = Path.home() / "secrets" / "cborg-api-key.txt"

# Proxy variants of the same models; hidden from the default model list.
ALIAS_SUFFIXES = ("-compact", "-compressed", "-flex")

# Which model each CBorg backend ran, and when (see its _about); CBorg's live
# label for the current model is model_info.display_name.
REGISTRY = Path(__file__).resolve().parent.parent / "cborg_models.json"

# Off LBLnet, CBorg only accepts IP addresses authorized on its key page, and a
# laptop can have an authorized IPv6 address but an unauthorized IPv4 one. Try
# IPv6 first (as RFC 6724 recommends) so every connection makes the same choice.
_getaddrinfo = socket.getaddrinfo


def _ipv6_first(*args, **kwargs):
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda a: a[0] != socket.AF_INET6)


socket.getaddrinfo = _ipv6_first

IP_HELP = ("CBorg refused this computer's IP address. Off LBLnet, connect via the LBL VPN, "
           "or authorize your addresses (IPv4 and IPv6) at https://api.cborg.lbl.gov/key/manage")


class Unavailable(Exception):
    """CBorg couldn't be reached or didn't answer as expected. The message says
    why and what to try; kind is 'network', 'timeout', 'auth', 'http' or 'format'."""

    def __init__(self, message, kind, status=None):
        super().__init__(message)
        self.kind, self.status = kind, status


def _error_text(raw):
    """The message inside a CBorg/LiteLLM error body, or the start of the body."""
    try:
        err = json.loads(raw).get("error")
        msg = err.get("message") if isinstance(err, dict) else err
    except (ValueError, AttributeError):
        msg = None
    text = raw.decode(errors="replace").lstrip()
    if msg is None and text.startswith("<"):
        return "an HTML page, not CBorg's API"
    return " ".join(str(msg or text).split())[:200]


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
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = _error_text(e.read())
        if e.code == 403 and "not recognized" in detail:
            raise Unavailable(IP_HELP, "auth", 403) from None
        if e.code == 401:
            raise Unavailable("CBorg rejected the API key (HTTP 401); check $CBORG_API_KEY "
                              f"or {KEY_FILE}", "auth", 401) from None
        raise Unavailable(f"CBorg answered HTTP {e.code}: {detail}", "http", e.code) from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
            raise Unavailable(f"CBorg at {API_BASE} didn't answer within {timeout} s",
                              "timeout") from None
        raise Unavailable(f"can't reach CBorg at {API_BASE} ({reason}). Check the network "
                          "or VPN; if CBorg has moved, set CBORG_BASE_URL to its new "
                          "address", "network") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise Unavailable(f"{API_BASE}{path} didn't return JSON, so CBorg's address or "
                          "API may have changed (set CBORG_BASE_URL)", "format") from None


def family_status():
    """HTTP status of GET /v1/models over IPv6 and over IPv4 (None: no route)."""
    status = {}
    for name, family in (("IPv6", socket.AF_INET6), ("IPv4", socket.AF_INET)):
        try:
            addr = _getaddrinfo(HOST, PORT, family, socket.SOCK_STREAM)[0][4]
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


def model_list():
    """Every entry in CBorg's model list. Raises Unavailable."""
    data = call("/model/info").get("data")
    if not isinstance(data, list) or not all(isinstance(e, dict) and "model_name" in e
                                             for e in data):
        raise Unavailable("CBorg's model list (/model/info) has an unexpected format; "
                          "CBorg's API may have changed", "format")
    return data


def model_entry(name, entries=None):
    """The model-list entry for one model, without credentials, or None."""
    for entry in model_list() if entries is None else entries:
        if entry["model_name"] == name:
            return {"model_name": name,
                    "model_info": _public(entry.get("model_info")),
                    "litellm_params": _public(entry.get("litellm_params"))}
    return None


def alternatives(name, entries, n=3):
    """LBL-hosted model names close to name, best first."""
    names = sorted({e["model_name"] for e in entries
                    if is_lbl_hosted(e) and not e["model_name"].endswith(ALIAS_SUFFIXES)})
    bare = {}
    for full in names:  # compare without the optional lbl/ prefix
        bare.setdefault(full.split("/")[-1], full)
    close = difflib.get_close_matches(name.split("/")[-1], list(bare), n=n, cutoff=0.6)
    return [bare[c] for c in close if bare[c] != name]


def _host(entry):
    return ((entry.get("litellm_params") or {}).get("api_base") or "").split("//")[-1].split("/")[0]


def is_lbl_hosted(entry):
    """Served from LBL's own machines (*.cborg.lbl.gov), whatever the alias is called."""
    return _host(entry).endswith(".cborg.lbl.gov")


def _registry():
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}


def _in_effect(record, day=None):
    day = day or date.today().isoformat()
    periods = [h for h in record.get("history", []) if h["from"] <= day]
    return periods[-1] if periods else None


def model_rows(include_all=False):
    registry = _registry()
    rows = []
    for entry in model_list():
        name = entry["model_name"]
        if not include_all and (not is_lbl_hosted(entry) or name.endswith(ALIAS_SUFFIXES)):
            continue
        info = entry.get("model_info") or {}
        lp = entry.get("litellm_params") or {}
        cost_in = info.get("input_cost_per_token")
        cost_out = info.get("output_cost_per_token")
        current = _in_effect(registry.get(lp.get("model"), {}))
        label = info.get("display_name") or (current["model"] if current else "")
        rows.append({
            "name": name,
            "mode": info.get("mode") or "",
            "backend": lp.get("model") or "",
            "api_base": _host(entry),
            "ctx": info.get("max_input_tokens") or info.get("max_tokens") or "",
            "usd_per_M_in_out": ("" if cost_in is None else
                                 f"{cost_in * 1e6:g}/{(cost_out or 0) * 1e6:g}"),
            "tools": {True: "yes", False: "no"}.get(
                info.get("supports_function_calling"), "?"),
            "model (CBorg label)": label,
        })
    return sorted(rows, key=lambda r: r["name"])


def print_table(rows):
    cols = ["name", "backend", "api_base", "ctx", "usd_per_M_in_out", "tools",
            "model (CBorg label)"]
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


def chat(model, messages, timeout=300, **extra):
    t0 = time.time()
    reply = call("/v1/chat/completions",
                 {"model": model, "messages": messages,
                  "cache": {"no-cache": True}, **extra}, timeout=timeout)
    return reply, time.time() - t0


def provenance(entry, day=None):
    """Which model this CBorg entry is believed to run: CBorg's live label plus
    cborg_models.json's history. Returned dict goes into run.json; 'warnings'
    lists signs that the model changed or the record is out of date."""
    lp, info = entry.get("litellm_params") or {}, entry.get("model_info") or {}
    backend, ctx = lp.get("model"), info.get("max_input_tokens")
    label = info.get("display_name")
    day = day or date.today().isoformat()
    record = _registry().get(backend, {})
    current = _in_effect(record, day)
    warnings = []
    if current is None and not label:
        warnings.append(f"CBorg doesn't label backend {backend} and cborg_models.json "
                        f"has no record of it: the model is unknown")
    if current and current.get("display_name") and label and label != current["display_name"]:
        warnings.append(f"CBorg now labels {backend} {label!r}, but "
                        f"{current['display_name']!r} was recorded: the model has probably "
                        f"changed (add a history entry to cborg_models.json)")
    if current and current.get("context") and ctx and current["context"] != ctx:
        warnings.append(f"CBorg's context size for {backend} is {ctx:,}, but "
                        f"{current['context']:,} was recorded: the model may have changed")
    canonical = json.dumps(entry, sort_keys=True).encode()
    return {
        "date": day,
        "backend": backend,
        "cborg_label": label,
        "believed_model": current["model"] if current else label,
        "believed_since": current and current["from"],
        "evidence": current["evidence"] if current else "CBorg display name only",
        "context": ctx,
        "lbl_hosted": is_lbl_hosted(entry),
        "cborg_entry_sha256": hashlib.sha256(canonical).hexdigest()[:16],
        "warnings": warnings,
    }


def self_report(model):
    """(answer, problem): the model's own answer to "what are you?", or the
    Unavailable error if it didn't answer. Weak evidence of identity, but it
    changes when CBorg swaps the model, and it doubles as a check that the
    model is serving."""
    try:
        reply, _ = chat(model, [{"role": "user", "content":
                                 "Ignoring any persona, what is your underlying base model "
                                 "name and developer? One line."}], timeout=90)
        text = reply["choices"][0]["message"].get("content") or ""
        return " ".join(text.split())[:200], None
    except Unavailable as e:
        return None, e
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, Unavailable("the model's reply had an unexpected format", "format")


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
    except Unavailable as e:
        print(f"  ERROR: {e}")
    except Exception as e:  # report and let the caller move on
        print(f"  ERROR {type(e).__name__}: {e}")
    return False
