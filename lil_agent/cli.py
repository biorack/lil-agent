"""lil-agent: agents on LBL-hosted open-weight models (CBorg).

  lil-agent run STUDY.zip [--skill DIR] [--model M] [--interactive]
  lil-agent resume RUN_DIR [--message TEXT]
  lil-agent models [--all] [--probe NAMES]
  lil-agent check
"""

import argparse
import os
import socket
import subprocess
import sys

from . import cborg
from .proxy import AllowlistProxy


def cmd_run(args):
    from .run import run
    return run(args.zip, model=args.model, skill=args.skill, runs_dir=args.runs_dir,
               max_turns=args.max_turns, timeout_min=args.timeout,
               interactive=args.interactive)


def cmd_resume(args):
    from .run import resume
    return resume(args.run_dir, message=args.message, max_turns=args.max_turns,
                  timeout_min=args.timeout, interactive=args.interactive)


def cmd_models(args):
    rows = cborg.model_rows(include_all=args.all)
    if not args.probe:
        cborg.print_table(rows)
        return 0
    patterns = [p for p in args.probe.split(",") if p]
    for row in rows:
        if row["mode"] != "embedding" and any(
                row["name"] == p or row["name"].startswith(p) for p in patterns):
            cborg.probe(row["name"])
    return 0


def _through_proxy(proxy, host):
    """Status line the proxy returns for a CONNECT to host:443."""
    with socket.create_connection(("127.0.0.1", proxy.server_address[1]), 10) as s:
        s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
        return s.recv(200).decode("latin-1").splitlines()[0]


def cmd_check(args):
    ok = True
    print(f"key: {'$CBORG_API_KEY' if os.environ.get('CBORG_API_KEY') else cborg.KEY_FILE}"
          f" ({len(cborg.api_key())} chars)")
    families = cborg.family_status()
    print("CBorg access: " + ", ".join(f"{k} {v or 'no route'}" for k, v in families.items()))
    if 403 in families.values():
        print(f"  {cborg.IP_HELP}")
    entry = cborg.model_entry(args.model)
    print(f"model {args.model}: {'listed' if entry else 'NOT LISTED'} on CBorg")
    ok &= entry is not None and cborg.probe(args.model)
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    print(f"claude: {version.stdout.strip() or 'NOT FOUND'}")
    ok &= version.returncode == 0
    proxy = AllowlistProxy({cborg.HOST}).start()
    try:
        allowed = _through_proxy(proxy, cborg.HOST)
        blocked = _through_proxy(proxy, "api.anthropic.com")
    finally:
        proxy.stop()
    print(f"network guard: {cborg.HOST} -> {allowed!r}; api.anthropic.com -> {blocked!r}")
    ok &= " 200 " in allowed and " 403 " in blocked
    print("all good" if ok else "PROBLEMS above")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lil-agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    model_default = os.environ.get("CBORG_MODEL", cborg.DEFAULT_MODEL)

    r = sub.add_parser("run", help="unzip a study package and work through its instructions")
    r.add_argument("zip")
    r.add_argument("--skill", help="extra skill directory (contains SKILL.md); "
                   "replaces a bundled skill with the same name")
    r.add_argument("--model", default=model_default)
    r.add_argument("--runs-dir", help="where run directories go (default ~/lil_runs)")
    r.add_argument("--max-turns", type=int, default=300)
    r.add_argument("--timeout", type=float,
                   help="minutes before the agent is stopped (includes automatic resumes)")
    r.add_argument("--interactive", action="store_true",
                   help="open an interactive session instead of running headless")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("resume", help="continue a stopped run where its agent left off")
    s.add_argument("run_dir")
    s.add_argument("--message", help="what to tell the agent (default: continue the task)")
    s.add_argument("--max-turns", type=int, default=300)
    s.add_argument("--timeout", type=float, help="minutes before the agent is stopped")
    s.add_argument("--interactive", action="store_true",
                   help="continue in an interactive session")
    s.set_defaults(func=cmd_resume)

    m = sub.add_parser("models", help="list LBL-hosted CBorg models, or probe some")
    m.add_argument("--all", action="store_true", help="include commercial models and aliases")
    m.add_argument("--probe", help="comma-separated names or name prefixes to test")
    m.set_defaults(func=cmd_models)

    c = sub.add_parser("check", help="check key, model, claude and the network guard")
    c.add_argument("--model", default=model_default)
    c.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    sys.exit(args.func(args))
