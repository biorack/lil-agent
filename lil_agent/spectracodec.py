"""Metadata and documents embedded in mzML files with SpectraCodec.

SpectraCodec (https://github.com/biorack/SpectraCodec) lays a JSON message onto
the first spectrum of an mzML file as extra peaks: structured metadata (run,
sample, methods, QC, often the whole dataset's run table) and complete
documents, each with its SHA-256, optionally signed (Ed25519) over both the
message and the acquired spectra.

lil-agent decodes every mzML in a run folder into decoded/ before the agent
starts, checks every document's hash and every file's signature, and tells the
agent that each file's first spectrum is the carrier. SpectraCodec is licensed
by Berkeley Lab for non-commercial use only, so it is not part of lil-agent:
it is downloaded once, pinned to a commit and checked by SHA-256 (or set
SPECTRACODEC_PY to a local spectra_codec.py).
"""

import base64
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import urllib.error
import urllib.request
import warnings
from pathlib import Path

COMMIT = "4058edb0facc50f1b15fe358d070ba10968f54c9"
PINNED = {
    "spectra_codec.py": "f9360e6bfef3c7898c0a0fe706be1a4d01e987b1a025aeccb4a42fc7de139721",
    "spectracodec_verification_key.pem":
        "ddb53e140f38330fb52e576875449f66830f97a148276a1b4efa64c68a9b4dc1",
    "license_and_copyright.txt": "761a83dffc9cf2e34bcd741bd76fc6bf4c31608a6b5bf0bb1c1754959aeadc16",
}
RAW = "https://raw.githubusercontent.com/biorack/SpectraCodec/{commit}/{name}"
# Published in SpectraCodec's README as the LBNL verification key.
LBNL_KEY_FINGERPRINT = "SHA256:lbrclU9WYbrXjl/AWl8VOWFUEm5EQgGybgdSKZzTwfA"


class CodecError(Exception):
    """SpectraCodec couldn't be obtained or loaded; the message says why."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _fetch(tools):
    tools.mkdir(parents=True, exist_ok=True)
    for name, digest in PINNED.items():
        path = tools / name
        if path.is_file() and _sha256(path.read_bytes()) == digest:
            continue
        url = RAW.format(commit=COMMIT, name=name)
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
        except (urllib.error.URLError, OSError) as e:
            raise CodecError(f"couldn't download SpectraCodec's {name} from GitHub "
                             f"({getattr(e, 'reason', e)})") from None
        if _sha256(data) != digest:
            raise CodecError(f"GitHub's {name} at commit {COMMIT[:12]} doesn't match the "
                             f"pinned SHA-256; not using it")
        path.write_bytes(data)


def load(runs_dir):
    """(module, verification key path, description of the codec used)."""
    local = os.environ.get("SPECTRACODEC_PY")
    if local:
        path = Path(local).expanduser()
        key = path.with_name("spectracodec_verification_key.pem")
        about = {"source": str(path), "commit": None}
    else:
        tools = runs_dir / ".tools" / f"spectracodec-{COMMIT[:12]}"
        _fetch(tools)
        path, key = tools / "spectra_codec.py", tools / "spectracodec_verification_key.pem"
        about = {"source": f"github.com/biorack/SpectraCodec@{COMMIT[:12]}", "commit": COMMIT}
    if not path.is_file():
        raise CodecError(f"no spectra_codec.py at {path}")
    about["sha256"] = _sha256(path.read_bytes())
    spec = importlib.util.spec_from_file_location("spectra_codec", path)
    module = importlib.util.module_from_spec(spec)
    try:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            spec.loader.exec_module(module)
    except ImportError as e:
        raise CodecError(f"SpectraCodec needs a Python package that's missing ({e}); it uses "
                         f"numpy, pandas, lxml, pymzml, pyteomics, requests, cryptography") from None
    return module, (key if key.is_file() else None), about


@contextlib.contextmanager
def _quiet():
    # pymzml prints index warnings for every file
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        warnings.simplefilter("ignore")
        yield


def _table(value):
    """(columns, rows) if value is a table: {columns, rows} or a list of dicts."""
    if isinstance(value, dict) and isinstance(value.get("columns"), list) \
            and isinstance(value.get("rows"), list):
        return value["columns"], value["rows"]
    if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
        cols = list(dict.fromkeys(k for r in value for k in r))
        return cols, [[r.get(c) for c in cols] for r in value]
    return None


def _cell(v):
    return v if v is None or isinstance(v, (str, int, float, bool)) else json.dumps(v)


def _write_csv(path, columns, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        w.writerows([[_cell(c) for c in r] for r in rows])


def decode_folder(run_dir, runs_dir, skip=("outputs", "decoded", ".lil", ".claude", ".git")):
    """Decode every mzML under run_dir into run_dir/decoded/. Returns a record for
    run.json, or None when there are no mzML files or none carries a message."""
    mzmls = sorted(p for p in run_dir.rglob("*")
                   if p.suffix.lower() == ".mzml" and p.is_file()
                   and not set(p.relative_to(run_dir).parts) & set(skip))
    if not mzmls:
        return None
    try:
        module, key, codec = load(runs_dir)
    except CodecError as e:
        print(f"warning: mzML files found but not checked for SpectraCodec messages: {e}")
        return {"error": str(e)}
    decoder = module.SpectraCodec()
    out = run_dir / "decoded"
    # The first message's metadata is the baseline; later files keep only the keys
    # that differ from it (messages repeat large tables, e.g. the run table).
    runs, docs, baseline, base_hash, differs = [], {}, None, {}, set()
    print(f"checking {len(mzmls)} mzML files for SpectraCodec messages", flush=True)

    for n, path in enumerate(mzmls, 1):
        rel = path.relative_to(run_dir).as_posix()
        try:
            with _quiet():
                raw = decoder.decode_message_from_file(str(path))
            msg = json.loads(raw) if raw else None
        except Exception:  # not encoded, or not decodable: either way, no message
            msg = None
        if not isinstance(msg, dict):
            runs.append({"file": rel, "encoded": False})
            continue
        signature = {"signed": False}
        if isinstance(msg.get("provenance"), dict):
            try:
                with _quiet():
                    v = module.verify_signed_file(str(path), str(key) if key else None)
                signature = {"signed": bool(v.get("signed")), "valid": bool(v.get("valid")),
                             "key_fingerprint": v.get("verification_key_fingerprint"),
                             "lbnl_key": v.get("verification_key_fingerprint") == LBNL_KEY_FINGERPRINT
                             and bool(v.get("valid")), "signed_at": v.get("signed_at")}
            except Exception as e:  # report, don't fail the run
                signature = {"signed": True, "valid": False, "error": f"{type(e).__name__}: {e}"}
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        names = []
        for f in payload.get("files") or []:
            try:
                data = (base64.b64decode(f["content"])
                        if f.get("content_encoding") == "base64" else f["content"].encode("utf-8"))
            except (KeyError, ValueError, AttributeError):
                continue
            ok = _sha256(data) == f.get("sha256")
            name = Path(f.get("filename") or "unnamed").name
            if f.get("scope") == "run_specific":
                dest = out / "files" / "runs" / path.stem / name
            else:
                dest = out / "files" / name
                if dest.exists() and _sha256(dest.read_bytes()) != _sha256(data):
                    dest = out / "files" / "variants" / path.stem / name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            entry = docs.setdefault(dest.relative_to(out).as_posix(),
                                    {"scope": f.get("scope"), "bytes": len(data),
                                     "sha256": _sha256(data), "hash_ok": True, "runs": 0})
            entry["hash_ok"] &= ok
            entry["runs"] += 1
            names.append(dest.relative_to(out).as_posix())
        meta = {k: v for k, v in payload.items() if k != "files"}
        if not payload:  # a message without the payload envelope: keep it whole
            meta = {k: v for k, v in msg.items() if k not in ("provenance", "unique_file_id")}
        hashes = {k: _sha256(json.dumps(v, sort_keys=True).encode()) for k, v in meta.items()}
        if baseline is None:
            baseline, base_hash, own = meta, hashes, {}
        else:
            own = {k: v for k, v in meta.items() if base_hash.get(k) != hashes[k]}
            differs |= set(own) | (set(base_hash) - set(meta))
        runs.append({"file": rel, "encoded": True, "unique_file_id": msg.get("unique_file_id"),
                     "signature": signature, "own": own, "keys": set(meta), "documents": names})
        if n % 50 == 0 or n == len(mzmls):
            print(f"  {n}/{len(mzmls)} checked", flush=True)

    encoded = [r for r in runs if r["encoded"]]
    if not encoded:
        return {"codec": codec, "mzml_files": len(mzmls), "encoded": 0}
    out.mkdir(exist_ok=True)
    (out / "runs").mkdir(exist_ok=True)
    varying = differs
    shared = {k: v for k, v in baseline.items() if k not in varying}
    common_files = []
    scalars = {k: v for k, v in shared.items() if not isinstance(v, (dict, list))}
    for k, value in sorted(shared.items()):
        if k in scalars:  # e.g. the payload format version: stated in the summary instead
            continue
        (out / f"{k}.json").write_text(json.dumps(value, indent=2))
        common_files.append(f"{k}.json")
        table = _table(value)
        if table:
            _write_csv(out / f"{k}.csv", *table)
            common_files.append(f"{k}.csv")
    columns, rows = ["file", "unique_file_id", "signature_valid"], []
    for r in encoded:
        stem = Path(r["file"]).stem
        own = {k: r["own"].get(k, baseline.get(k)) for k in sorted(varying) if k in r["keys"]}
        (out / "runs" / f"{stem}.json").write_text(json.dumps(
            {"file": r["file"], "unique_file_id": r["unique_file_id"],
             "signature": r["signature"], "metadata": own, "documents": r["documents"]}, indent=2))
        row = {"file": r["file"], "unique_file_id": r["unique_file_id"],
               "signature_valid": r["signature"].get("valid")}
        for k, v in own.items():
            for sub, val in (v.items() if isinstance(v, dict) else [(None, v)]):
                col = f"{k}.{sub}" if sub else k
                if col not in columns:
                    columns.append(col)
                row[col] = val
        rows.append([row.get(c) for c in columns])
    rows = [r + [None] * (len(columns) - len(r)) for r in rows]
    _write_csv(out / "runs.csv", columns, rows)

    sig = [r["signature"] for r in encoded]
    counts = {"lbnl_key_valid": sum(s.get("lbnl_key", False) for s in sig),
              "other_key_valid": sum(s.get("valid", False) and not s.get("lbnl_key") for s in sig),
              "invalid": sum(s.get("signed", False) and not s.get("valid") for s in sig),
              "unsigned": sum(not s.get("signed") for s in sig)}
    bad_docs = [k for k, d in docs.items() if not d["hash_ok"]]
    lines = [
        "# Metadata decoded from the mzML files (SpectraCodec)", "",
        f"lil-agent checked {len(mzmls)} mzML files for SpectraCodec messages; "
        f"{len(encoded)} carry one.", "",
        "**In each of those files the first spectrum is the carrier: it holds the message as "
        "artificial peaks. Skip the first spectrum when analyzing spectra.**", "",
        "Signatures (a valid one means neither the metadata nor the acquired spectra changed "
        f"since signing): {counts['lbnl_key_valid']} valid with the LBNL SpectraCodec key "
        f"({LBNL_KEY_FINGERPRINT}), {counts['other_key_valid']} valid with another key, "
        f"{counts['invalid']} invalid, {counts['unsigned']} unsigned.", "",
        "## Metadata", "",
        "- Same in every file: " + (", ".join(f"`{f}`" for f in common_files) or "none")
        + "".join(f"; {k} = `{v}`" for k, v in sorted(scalars.items())),
        "- Per file: `runs.csv` (one row per file) and `runs/<file>.json`", "",
        "## Documents (`files/`)", "",
    ]
    if docs:
        lines += ["| file | scope | bytes | SHA-256 matches | files carrying it |", "|---|---|---|---|---|"]
        for k, d in sorted(docs.items()):
            lines.append(f"| `{k}` | {d['scope'] or ''} | {d['bytes']:,} | "
                         f"{'yes' if d['hash_ok'] else '**NO**'} | {d['runs']} |")
    else:
        lines.append("None.")
    lines += ["", f"Decoded with SpectraCodec from {codec['source']} "
              f"(spectra_codec.py SHA-256 {codec['sha256'][:16]}…)."]
    (out / "SPECTRACODEC.md").write_text("\n".join(lines) + "\n")
    print(f"decoded {len(encoded)} SpectraCodec messages into decoded/: {len(docs)} documents; "
          f"signatures {counts['lbnl_key_valid']} valid (LBNL key), "
          f"{counts['other_key_valid']} valid (other key), {counts['invalid']} invalid, "
          f"{counts['unsigned']} unsigned" + (f"; DOCUMENT HASH MISMATCH: {bad_docs}" if bad_docs else ""))
    return {"codec": codec, "mzml_files": len(mzmls), "encoded": len(encoded),
            "signatures": counts, "documents": docs, "shared_metadata": sorted(shared),
            "per_file_metadata": sorted(varying)}
