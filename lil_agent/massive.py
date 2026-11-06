"""MassIVE (https://massive.ucsd.edu) datasets as lil-agent inputs.

`lil-agent run MSV000103868` looks the dataset up through MassIVE's PROXI API
(which gives the current FTP location, so the changing vNN in the path never
needs hard-coding), lists its files over FTPS, downloads the selected ones into
a cache, and builds a run folder: MASSIVE.md (what the dataset is),
massive_files.csv (every file), INSTRUCTIONS.md (the task) and data/ (the
downloaded files, hard-linked from the cache). The agent then works offline as
with any study package.

Only lil-agent's own process talks to MassIVE, and only to read public
metadata and download public files.
"""

import csv
import fnmatch
import ftplib
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path, PurePosixPath

PROXI = "https://massive.ucsd.edu/ProteoSAFe/proxi/v0.1/datasets/{acc}"
PAGE = "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={acc}"
LINK = "https://massive.ucsd.edu/ProteoSAFe/QueryMSV?id={acc}"
FTP_HOST = "massive-ftp.ucsd.edu"  # the API sometimes names massive.ucsd.edu, which has no FTP

# Mass spectrometry data files; everything else that is small is metadata.
DATA_SUFFIXES = {".mzml", ".mzxml", ".mzdata", ".raw", ".wiff", ".wiff2", ".scan", ".mgf",
                 ".d", ".baf", ".tdf", ".tdf_bin", ".imzml", ".ibd", ".mzml.gz", ".mzxml.gz",
                 ".raw.zip", ".d.zip"}
METADATA_MAX_BYTES = 50_000_000
DISK_MARGIN_BYTES = 2_000_000_000

ACCESSION = re.compile(r"^MSV\d{9}$", re.I)


class MassiveError(Exception):
    """A MassIVE lookup or download failed; the message says why and what to try."""


def is_accession(text):
    return bool(ACCESSION.match(str(text).strip()))


def _suffix(path):
    name = PurePosixPath(path).name.lower()
    for s in sorted(DATA_SUFFIXES, key=len, reverse=True):
        if name.endswith(s):
            return s
    return PurePosixPath(name).suffix


def is_data(path):
    return _suffix(path) in DATA_SUFFIXES


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "lil-agent"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise MassiveError(f"MassIVE answered HTTP {e.code} for {url}") from None
    except (urllib.error.URLError, OSError) as e:
        raise MassiveError(f"can't reach MassIVE ({getattr(e, 'reason', e)}); check the "
                           f"network") from None


def _values(items, name=None):
    """Flatten PROXI's lists of CV terms into their values (or names)."""
    out = []
    for item in items or []:
        for term in item if isinstance(item, list) else [item]:
            if isinstance(term, dict) and (name is None or term.get("name") == name):
                value = term.get("value") or term.get("name")
                if value and value not in out:
                    out.append(value)
    return out


def dataset_info(acc):
    """Title, summary and descriptive terms from PROXI, and the FTP location."""
    acc = acc.strip().upper()
    try:
        meta = json.loads(_get(PROXI.format(acc=acc)))
    except ValueError:
        meta = {}
    except MassiveError as e:
        if "HTTP 404" not in str(e):
            raise
        meta = {}
    if not isinstance(meta, dict) or not meta.get("title"):
        raise MassiveError(f"MassIVE has no public dataset {acc} (or its API changed); check "
                           f"{LINK.format(acc=acc)}")
    ftp_url = next((v for v in _values(meta.get("datasetLink")) if str(v).startswith("ftp://")),
                   None)
    if not ftp_url:  # fall back to the dataset page's download link
        page = _get(PAGE.format(acc=acc)).decode(errors="replace")
        m = re.search(rf"ftp://[\w.-]+/(?:[\w.-]+/)*{acc}/?", page)
        ftp_url = m.group(0) if m else None
    if not ftp_url:
        raise MassiveError(f"couldn't find {acc}'s FTP location on MassIVE; see "
                           f"{LINK.format(acc=acc)}")
    path = "/" + ftp_url.split("://", 1)[1].split("/", 1)[1].strip("/")
    return {
        "accession": acc,
        "title": meta.get("title"),
        "summary": meta.get("summary"),
        "instruments": _values(meta.get("instruments")),
        "species": _values(meta.get("species")),
        "keywords": _values(meta.get("keywords")),
        "publications": _values(meta.get("publications")),
        "page": LINK.format(acc=acc),
        "ftp": f"ftp://{FTP_HOST}{path}/",
        "ftp_path": path,
    }


def _connect():
    try:
        ftp = ftplib.FTP_TLS(FTP_HOST, timeout=120)
        ftp.login()
        ftp.prot_p()
        return ftp
    except (ftplib.all_errors + (OSError,)) as e:
        raise MassiveError(f"can't connect to MassIVE's FTP server {FTP_HOST} ({e})") from None


def list_files(info):
    """Every file in the dataset: [(relative path, size, modify time)]."""
    ftp, root, files = _connect(), info["ftp_path"], []

    def walk(path):
        for name, facts in ftp.mlsd(path):
            if name in (".", ".."):
                continue
            kind = facts.get("type", "").lower()
            if kind == "dir":
                walk(f"{path}/{name}")
            elif kind == "file":
                files.append((f"{path}/{name}"[len(root) + 1:], int(facts.get("size", 0)),
                              facts.get("modify", "")))
    try:
        walk(root)
    except (ftplib.all_errors + (OSError,)) as e:
        raise MassiveError(f"couldn't list {info['ftp']} ({e})") from None
    finally:
        try:
            ftp.quit()
        except (ftplib.all_errors + (OSError,)):
            pass
    return sorted(files)


def _gb(n):
    return f"{n / 1e9:.2f} GB"


def folder_summary(files, selected=()):
    chosen = {p for p, _, _ in selected}
    groups = defaultdict(lambda: [0, 0, 0])
    for p, s, _ in files:
        key = "/".join(p.split("/")[:2]) if p.count("/") >= 2 else (p.split("/")[0])
        groups[key][0] += 1
        groups[key][1] += s
        groups[key][2] += p in chosen
    return sorted(groups.items())


def print_inventory(info, files):
    print(f"{info['accession']}: {info['title']}")
    print(f"  {info['page']}")
    print(f"  {info['ftp']}")
    print(f"  {len(files)} files, {_gb(sum(s for _, s, _ in files))}")
    for key, (n, size, _) in folder_summary(files):
        print(f"  {n:6d} files {_gb(size):>10}  {key}")
    print(f"  free disk where runs go: {_gb(shutil.disk_usage(Path.home()).free)}")


def plan(files, include, exclude, max_gb, cache, acc="ACC"):
    """The files to download: small metadata always, data files by --include.
    Exits with guidance when the selection is empty, too big or won't fit."""
    def matches(path, patterns):
        return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(PurePosixPath(path).name, p)
                   for p in patterns)
    meta = [f for f in files if not is_data(f[0]) and f[1] <= METADATA_MAX_BYTES]
    data = [f for f in files if is_data(f[0])]
    if include:
        data = [f for f in data if matches(f[0], include)]
    data = [f for f in data if not matches(f[0], exclude)]
    total = sum(s for _, s, _ in data)
    limit = max_gb * 1e9
    examples = ("  examples: --include '*POS*'   --include 'raw/<folder>/*'   "
                f"--exclude '*Blank*'   --max-gb 20\n  see everything: lil-agent run "
                f"{acc} --list")
    if not include and total > limit:
        raise SystemExit(f"lil-agent: this dataset's data files total {_gb(total)}, over the "
                         f"{max_gb:g} GB limit. Choose files with --include (shell-style "
                         f"patterns on the file path) or raise --max-gb.\n{examples}")
    if include and not data:
        raise SystemExit(f"lil-agent: no data files match {include}"
                         + (f" after excluding {exclude}" if exclude else "") + f".\n{examples}")
    if total > limit:
        raise SystemExit(f"lil-agent: the selected data files total {_gb(total)} "
                         f"({len(data)} files), over the {max_gb:g} GB limit. Narrow the "
                         f"selection or raise --max-gb.\n{examples}")
    cached = sum(s for p, s, _ in data + meta if (cache / p).is_file()
                 and (cache / p).stat().st_size == s)
    need = sum(s for _, s, _ in data + meta) - cached
    free = shutil.disk_usage(cache if cache.exists() else Path.home()).free
    if need + DISK_MARGIN_BYTES > free:
        raise SystemExit(f"lil-agent: the download needs {_gb(need)} but only {_gb(free)} is "
                         f"free (keeping a {_gb(DISK_MARGIN_BYTES)} margin). Narrow the "
                         f"selection, free space, or point --runs-dir at a bigger disk.")
    return meta + data


def download(info, selection, cache):
    """Fetch the selection into the cache, resuming partial files and skipping
    complete ones; checks every size against the listing."""
    todo = [(p, s) for p, s, _ in selection
            if not ((cache / p).is_file() and (cache / p).stat().st_size == s)]
    total = sum(s for _, s in todo)
    have = len(selection) - len(todo)
    print(f"downloading {len(todo)} files ({_gb(total)}) from {info['ftp']}"
          + (f"; {have} already cached" if have else ""), flush=True)
    if not todo:
        return
    ftp, done, started, last_report = _connect(), 0, time.time(), 0.0
    for i, (rel, size) in enumerate(todo, 1):
        target = cache / rel
        part = target.with_name(target.name + ".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(4):
            try:
                offset = part.stat().st_size if part.exists() else 0
                with open(part, "ab") as fh:
                    ftp.retrbinary(f"RETR {info['ftp_path']}/{rel}", fh.write,
                                   blocksize=1 << 20, rest=offset or None)
                if part.stat().st_size != size:
                    raise MassiveError(f"{rel}: got {part.stat().st_size} bytes, expected {size}")
                part.replace(target)
                target.chmod(0o444)  # the cache is shared by runs; keep it read-only
                break
            except (ftplib.all_errors + (OSError, MassiveError)) as e:
                if attempt == 3:
                    raise MassiveError(f"couldn't download {rel} ({e}); rerun the same command "
                                       f"to resume") from None
                if isinstance(e, MassiveError):
                    part.unlink(missing_ok=True)
                time.sleep(5 * (attempt + 1))
                try:
                    ftp.close()
                except (ftplib.all_errors + (OSError,)):
                    pass
                ftp = _connect()
        done += size
        elapsed = time.time() - started
        if i == len(todo) or len(todo) <= 20 or elapsed - last_report > 15:
            rate = done / max(elapsed, 1e-6)
            eta = (total - done) / rate if rate else 0
            print(f"  {i}/{len(todo)} files, {_gb(done)} of {_gb(total)}, {rate / 1e6:.1f} MB/s"
                  + (f", about {eta / 60:.0f} min left" if i < len(todo) else ""), flush=True)
            last_report = elapsed
    try:
        ftp.quit()
    except (ftplib.all_errors + (OSError,)):
        pass


DEFAULT_TASK = """\
This folder holds files from the public MassIVE dataset {accession}, "{title}". MASSIVE.md \
describes the dataset (the submitter's summary, instruments, species, keywords) and which of \
its files were downloaded into data/; massive_files.csv lists every file in the dataset.

Metadata and documents that lil-agent decoded from the mzML files, if any, are in decoded/: \
treat them as the experiment's own description, and follow any instructions or analysis plan \
among them. Work out what experiment this is, then analyze the downloaded data as far as it \
allows, following any installed skills where they fit. Say clearly what the downloaded subset can \
and cannot show about the dataset as a whole.
"""


def populate(run_dir, info, files, selection, cache, include, exclude, task=None):
    """Write MASSIVE.md, massive_files.csv, INSTRUCTIONS.md and data/ into the run folder."""
    chosen = {p for p, _, _ in selection}
    data_dir = run_dir / "data"
    for rel in sorted(chosen):
        dest = data_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(cache / rel, dest)  # no extra disk; same file as the cache
        except OSError:
            shutil.copy2(cache / rel, dest)

    with open(run_dir / "massive_files.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "bytes", "modified", "downloaded"])
        for p, s, m in files:
            w.writerow([p, s, m, "yes" if p in chosen else "no"])

    sel_bytes = sum(s for _, s, _ in selection)
    lines = [f"# {info['accession']}: {info['title']}", "",
             f"Public MassIVE dataset. Page: {info['page']}  ", f"FTP: {info['ftp']}", ""]
    if info.get("summary"):
        lines += ["## Submitter's summary", "", info["summary"].strip(), ""]
    for label, key in (("Instruments", "instruments"), ("Species", "species"),
                       ("Keywords", "keywords"), ("Publications", "publications")):
        if info.get(key):
            lines += [f"**{label}:** " + "; ".join(map(str, info[key])) + "  "]
    lines += ["", "## What was downloaded", "",
              f"{len(chosen)} of {len(files)} files ({_gb(sel_bytes)} of "
              f"{_gb(sum(s for _, s, _ in files))}), under `data/` with the dataset's own paths. "
              f"Selection: include {include or 'all data files'}"
              + (f", exclude {exclude}" if exclude else "")
              + "; small metadata files are always included.", "",
              "| folder | files | size | downloaded |", "|---|---|---|---|"]
    for key, (n, size, got) in folder_summary(files, selection):
        lines.append(f"| `{key}` | {n} | {_gb(size)} | {got} |")
    (run_dir / "MASSIVE.md").write_text("\n".join(lines) + "\n")

    text = Path(task).read_text() if task and Path(task).expanduser().is_file() else task
    (run_dir / "INSTRUCTIONS.md").write_text(
        (text.strip() + "\n") if text else DEFAULT_TASK.format(**info))

    listing = hashlib.sha256(json.dumps(files).encode()).hexdigest()[:16]
    return {"type": "massive", "accession": info["accession"], "title": info["title"],
            "page": info["page"], "ftp": info["ftp"], "listed_files": len(files),
            "listed_bytes": sum(s for _, s, _ in files), "listing_sha256": listing,
            "include": list(include), "exclude": list(exclude),
            "downloaded_files": len(chosen), "downloaded_bytes": sel_bytes,
            "task": "custom" if text else "default"}


def prepare_source(acc, runs_dir, include, exclude, max_gb, list_only):
    """Look up and list a dataset; with list_only, print the inventory and stop.
    Returns (info, files, selection, cache) ready for download()."""
    try:
        info = dataset_info(acc)
        files = list_files(info)
    except MassiveError as e:
        sys.exit(f"lil-agent: {e}")
    if list_only:
        print_inventory(info, files)
        return None
    cache = runs_dir / ".massive" / info["accession"]
    selection = plan(files, include, exclude, max_gb, cache, info["accession"])
    return info, files, selection, cache
