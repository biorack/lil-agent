"""lil-agent run: unzip a study package and have an agent work through it.

The agent is Claude Code (headless) driven by an LBL-hosted model on CBorg.
Two guards keep everything at LBL:

- Claude Code's own traffic goes through a local proxy that only lets
  connections to api.cborg.lbl.gov through and logs every attempt
  (.lil/network.log).
- The agent's shell commands run in Claude Code's sandbox with no network
  and no read access to credentials; WebFetch and WebSearch are disabled.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import cborg
from .proxy import AllowlistProxy

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CBORG = ROOT / "cborg" / "claude-cborg"

# Tools the agent doesn't get. Skill is off so that only the package's skills
# (read as files) apply, not the skills built into Claude Code.
DISALLOWED = ["WebFetch", "WebSearch", "Skill"]

# The agent's shell commands may not read these.
PRIVATE = ["~/secrets", "~/.ssh", "~/.aws", "~/.config/gcloud", "~/.claude"]

# CBorg sits behind Cloudflare, which gives up on a reply after 120 s (524);
# these API errors are temporary, so the session is resumed after a pause.
TRANSIENT = {429, 500, 502, 503, 504, *range(520, 530)}
RETRIES = 3
RETRY_WAIT_S = 120

PROMPT = """\
This folder is a study package unzipped from {zip_name}. Find the instructions in it \
and carry them out completely. No one is available to answer questions: when something \
is ambiguous, make a reasonable choice and record it.

- Shell commands run in a sandbox with no network access. Run one simple command per \
Bash call: commands chained with &&, ; or | are rejected here, so put multi-step work in \
a script.
- Do all computation in Python (python3 has pandas, numpy, scipy, statsmodels, \
scikit-learn, matplotlib and seaborn). Save each analysis script under outputs/code/ \
and run it from there, so the analysis can be rerun.
- Never type numbers into a report by hand: take them from files your code wrote.
- Put everything you produce under outputs/, and keep a log of what you did and why \
in outputs/notes.md.
{memory_line}
{skill_line}
Finish with a short summary of what you produced and anything you could not do."""

MEMORY_LINE = """\
- This computer has {ram} GB of memory, shared with other programs: keep each Python \
process under about {budget} GB. Check file sizes before loading anything large; read only \
the columns you need, use float32 for intensities, read big files in chunks, and free \
tables you no longer need. A script that dies with "Killed: 9" (exit code 137 or -9) ran \
out of memory: make it leaner rather than rerunning it."""

RESUME_MESSAGE = """\
Your previous session stopped before you finished ({reason}). Look at outputs/notes.md \
and what is already in outputs/, then continue the task from where you left off.
{memory_line}"""


def settings():
    """Claude Code project settings written into each run directory."""
    return {
        "permissions": {
            "defaultMode": "acceptEdits",
            "deny": ["WebFetch", "WebSearch"] + [f"Read({p}/**)" for p in PRIVATE],
        },
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"denyRead": PRIVATE},
            "network": {"allowedDomains": [], "strictAllowlist": True},
            "credentials": {"envVars": [{"name": "ANTHROPIC_AUTH_TOKEN",
                                         "mode": "deny"}]},
        },
    }


def git(run_dir, *args):
    subprocess.run(["git", "-C", str(run_dir), *args],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install_skills(run_dir, extra=None):
    """Copy skills bundled in the package (folders containing SKILL.md), then
    the --skill folder if given, into .claude/skills/ where Claude Code looks.
    A --skill folder replaces a bundled skill with the same name."""
    sources = {}
    for skill_md in sorted(run_dir.rglob("SKILL.md")):
        if ".claude" not in skill_md.relative_to(run_dir).parts:
            src = skill_md.parent
            sources[src.name if src != run_dir else "package"] = src
    if extra:
        sources[extra.name] = extra
    target = run_dir / ".claude" / "skills"
    for name, src in sources.items():
        if (target / name).exists():
            shutil.rmtree(target / name)
        shutil.copytree(src, target / name,
                        ignore=shutil.ignore_patterns(".claude", ".lil", ".git", "outputs"))
    return sorted(sources)


def prepare(zip_path, model, skill, runs_dir):
    slug = zip_path.stem
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = runs_dir / f"{slug}-{model.split('/')[-1]}-{stamp}"
    (run_dir / ".lil").mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(run_dir)
    (run_dir / ".claude").mkdir(exist_ok=True)
    (run_dir / ".claude" / "settings.json").write_text(json.dumps(settings(), indent=2))
    skills = install_skills(run_dir, skill)
    (run_dir / "outputs").mkdir(exist_ok=True)
    (run_dir / ".gitignore").write_text(".lil/mpl/\n")
    # A git repo shows exactly what the agent created or changed.
    git(run_dir, "init", "-q")
    git(run_dir, "add", "-A")
    git(run_dir, "commit", "-qm", "study package as unzipped")
    return run_dir, skills


def _brief(tool, args):
    if tool == "Bash":
        return args.get("command", "").replace("\n", " ")[:140]
    for key in ("file_path", "path", "pattern"):
        if key in args:
            return str(args[key])
    if tool == "TodoWrite":
        return f"{len(args.get('todos', []))} items"
    return json.dumps(args)[:140]


def _show(event, state):
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        print(f"session started: model {event.get('model')}, "
              f"{len(event.get('tools', []))} tools", flush=True)
    elif kind == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                state["tools"] += 1
                print(f"  [{state['tools']:3d}] {block['name']}: "
                      f"{_brief(block['name'], block.get('input', {}))}", flush=True)
            elif block.get("type") == "text" and block.get("text", "").strip():
                text = " ".join(block["text"].split())
                print(f"  > {text[:200]}{'...' if len(text) > 200 else ''}", flush=True)
    elif kind == "user":
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("is_error"):
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content
                                       if isinstance(c, dict))
                print(f"        ! {' '.join(str(content).split())[:160]}", flush=True)


def memory_line():
    try:
        ram = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True).stdout)
    except (OSError, ValueError):  # no sysctl, or no hw.memsize (Linux)
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    ram = round(ram / 2**30)
    return MEMORY_LINE.format(ram=ram, budget=max(2, ram // 4))


def _stream(cmd, run_dir, env, timeout_s):
    """Run headless Claude Code; append its stream-json events to the transcript."""
    lil = run_dir / ".lil"
    state = {"tools": 0}
    result, session_id = None, None
    with open(lil / "transcript.jsonl", "a") as transcript, \
         open(lil / "stderr.log", "a") as stderr:
        proc = subprocess.Popen(cmd, cwd=run_dir, env=env, text=True, bufsize=1,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=stderr)
        timed_out = threading.Event()

        def stop_on_timeout():
            timed_out.set()
            proc.kill()

        timer = None
        if timeout_s:
            timer = threading.Timer(timeout_s, stop_on_timeout)
            timer.start()
        try:
            for line in proc.stdout:
                transcript.write(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = session_id or event.get("session_id")
                if event.get("type") == "result":
                    result = event
                else:
                    _show(event, state)
            rc = proc.wait()
        finally:
            # Also reached on Ctrl-C: don't leave the agent running.
            if timer:
                timer.cancel()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return result, rc, ("timeout" if timed_out.is_set() else None), session_id


def _transient(result):
    if not result or not result.get("is_error"):
        return False
    return (result.get("api_error_status") in TRANSIENT or
            bool(re.search(r"API Error: (429|5\d\d)|overloaded", result.get("result") or "", re.I)))


def _session_from_transcript(run_dir):
    path = run_dir / ".lil" / "transcript.jsonl"
    if path.exists():
        for line in open(path):
            try:
                sid = json.loads(line).get("session_id")
            except json.JSONDecodeError:
                continue
            if sid:
                return sid
    return None


def _what_to_try(model, entries, run_dir=None):
    """Lines suggesting other models when this one is unavailable."""
    lines = []
    close = cborg.alternatives(model, entries) if entries else []
    if close:
        lines.append(f"  similar models: {', '.join(close)}")
    if model != cborg.DEFAULT_MODEL and (entries is None or any(
            e["model_name"] == cborg.DEFAULT_MODEL for e in entries)):
        lines.append(f"  lil-agent's default: {cborg.DEFAULT_MODEL}")
    lines.append("  all LBL-hosted models: lil-agent models; "
                 "test one: lil-agent models --probe NAME")
    if run_dir:
        lines.append(f"  continue this run with another model: "
                     f"lil-agent resume {run_dir} --model NAME")
    return lines


def _check_model(model, run_dir=None):
    """CBorg's entry for the model and its self-report, after checking that it
    is listed and answers. Exits with suggestions if it isn't usable."""
    entries, entry = None, None
    try:
        entries = cborg.model_list()
        entry = cborg.model_entry(model, entries)
    except cborg.Unavailable as e:
        if e.kind in ("auth", "network"):
            sys.exit(f"lil-agent: {e}")
        print(f"warning: couldn't read CBorg's model list: {e}\n"
              f"  continuing without checking or recording the model", file=sys.stderr)
    if entries is not None and entry is None:
        sys.exit("\n".join([f"lil-agent: CBorg doesn't list a model called {model!r}."]
                           + _what_to_try(model, entries, run_dir)))

    answer, problem = cborg.self_report(model)
    if problem is None:
        return entry, answer
    if problem.kind == "timeout" or problem.status in TRANSIENT:
        print(f"warning: {model} didn't answer a test request ({problem}); starting "
              f"anyway, since temporary CBorg errors are retried", file=sys.stderr)
        return entry, None
    if problem.kind in ("auth", "network"):
        sys.exit(f"lil-agent: {problem}")
    if entries is None:
        sys.exit(f"lil-agent: {model} isn't answering ({problem}), and CBorg's model list "
                 f"was unavailable too, so CBorg's address may have changed: check "
                 f"{cborg.API_BASE} or set CBORG_BASE_URL")
    sys.exit("\n".join([f"lil-agent: {model} is in CBorg's list but isn't answering: "
                        f"{problem}"] + _what_to_try(model, entries, run_dir)))


def _model_provenance(model, entry, self_report):
    """Record which model this session is believed to use, and say so."""
    if entry:
        prov = cborg.provenance(entry)
    else:
        prov = {"backend": None, "cborg_label": None, "believed_model": None,
                "believed_since": None, "context": None,
                "warnings": ["CBorg's model list was unavailable, so the model's identity "
                             "wasn't checked"]}
    prov["model"] = model
    prov["self_report"] = self_report
    believed = prov["believed_model"] or "unknown model"
    print(f"model: {model} -> {believed}"
          + (f" (recorded {prov['believed_since']})" if prov["believed_since"] else ""))
    print(f"  CBorg label: {prov['cborg_label']}; self-report: {prov['self_report']}")
    for w in prov["warnings"]:
        print(f"  warning: {w}")
    return prov


def _after_error(result, rc, run_dir):
    """A line saying what to do after a session that ended on an error."""
    status = (result or {}).get("api_error_status")
    text = (result or {}).get("result") or ""
    if result is None:
        return (f"Claude Code stopped without a result (exit {rc}); see "
                f"{run_dir}/.lil/stderr.log")
    if status in TRANSIENT:
        return "CBorg kept failing with temporary errors; try again later with lil-agent resume"
    if status in (401, 403):
        return cborg.IP_HELP if "not recognized" in text else "CBorg rejected the API key"
    if status:
        return (f"the model stopped answering (API error {status}); continue with another "
                f"model: lil-agent resume {run_dir} --model NAME (see lil-agent models)")
    if re.search(r"connection|ECONN|fetch failed|network", text, re.I):
        return (f"Claude Code couldn't reach CBorg at {cborg.API_BASE}; check the network "
                f"or VPN (or CBORG_BASE_URL), then lil-agent resume")
    return None


def _execute(run_dir, model, message, entry, self_report, session_id=None, max_turns=300,
             timeout_min=None, interactive=False):
    """Run (or resume) the agent behind the network guard; record and summarize."""
    lil = run_dir / ".lil"
    prov = _model_provenance(model, entry, self_report)
    proxy = AllowlistProxy({cborg.HOST}, lil / "network.log").start()
    env = dict(os.environ)
    for k in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env.update(HTTPS_PROXY=proxy.url, https_proxy=proxy.url,
               HTTP_PROXY=proxy.url, http_proxy=proxy.url,
               CBORG_MODEL=model, MPLCONFIGDIR=str(lil / "mpl"))
    if prov["context"] and "CBORG_CONTEXT_TOKENS" not in os.environ:
        # Claude Code's auto-compact needs the real window; CBorg changes it.
        env["CBORG_CONTEXT_TOKENS"] = str(prov["context"])

    path = lil / "run.json"
    info = json.loads(path.read_text()) if path.exists() else {}
    if "attempts" not in info:
        # run.json from before attempts were recorded: keep its one session.
        info["attempts"] = [{k: info.get(k) for k in
                             ("started", "elapsed_s", "exit_code", "num_turns", "stop")}
                            ] if "elapsed_s" in info else []
    info.setdefault("model_provenance", prov)
    info["claude_code"] = subprocess.run(["claude", "--version"], capture_output=True,
                                         text=True).stdout.strip()
    started = time.time()
    deadline = started + timeout_min * 60 if timeout_min else None
    result, rc, stop, tries = None, None, None, 0
    try:
        while True:
            attempt_start = time.time()
            if interactive:
                cmd = [str(CLAUDE_CBORG), "--disallowedTools", *DISALLOWED]
                cmd += ["--resume", session_id] if session_id else []
                rc = subprocess.call(cmd + ["--", message], cwd=run_dir, env=env)
            else:
                cmd = [str(CLAUDE_CBORG), "-p", message,
                       "--output-format", "stream-json", "--verbose",
                       "--max-turns", str(max_turns),
                       "--permission-mode", "acceptEdits",
                       "--disallowedTools", *DISALLOWED]
                if session_id:
                    cmd += ["--resume", session_id]
                remaining = deadline - time.time() if deadline else None
                result, rc, stop, sid = _stream(cmd, run_dir, env, remaining)
                session_id = sid or session_id
            info["attempts"].append({
                "started": datetime.fromtimestamp(attempt_start).isoformat(timespec="seconds"),
                "elapsed_s": round(time.time() - attempt_start),
                "resumed": bool(session_id) and (tries > 0 or bool(info["attempts"])),
                "exit_code": rc,
                "num_turns": (result or {}).get("num_turns"),
                "stop": stop or (result or {}).get("subtype"),
                "api_error_status": (result or {}).get("api_error_status"),
                "model_provenance": prov,
            })
            if not (_transient(result) and session_id and tries < RETRIES):
                break
            tries += 1
            wait = RETRY_WAIT_S * tries
            if deadline and time.time() + wait > deadline:
                break
            print(f"\nCBorg error {result.get('api_error_status') or ''}, a temporary "
                  f"gateway problem; resuming the session in {wait} s "
                  f"(retry {tries} of {RETRIES})", flush=True)
            time.sleep(wait)
            message = RESUME_MESSAGE.format(reason="the model server timed out",
                                            memory_line=memory_line())
    except KeyboardInterrupt:
        print("\ninterrupted; saving what the agent produced so far", file=sys.stderr)
        rc, stop = 130, "interrupted"
    elapsed = time.time() - started
    proxy.stop()

    info.update({
        "session_id": session_id,
        "elapsed_s": sum(a.get("elapsed_s") or 0 for a in info["attempts"]),
        "exit_code": rc,
        "num_turns": (result or {}).get("num_turns"),
        "is_error": (result or {}).get("is_error"),
        "stop": stop or (result or {}).get("subtype"),
        "network": proxy.summary(),
    })
    path.write_text(json.dumps(info, indent=2))
    git(run_dir, "add", "-A")
    git(run_dir, "commit", "-qm", f"lil-agent session with {model}")

    print()
    if result and result.get("result"):
        print(result["result"].strip())
        print()
    outputs = [p for p in (run_dir / "outputs").rglob("*") if p.is_file()]
    print(f"run directory: {run_dir}")
    print(f"this session: {elapsed / 60:.1f} min, turns {info['num_turns']}, "
          f"exit {rc}, stop {info['stop']}"
          + (f", {tries} automatic resume(s)" if tries else ""))
    print(f"outputs: {len(outputs)} files under outputs/")
    if info["is_error"] or (rc not in (0, None) and info["stop"] not in ("interrupted",
                                                                          "timeout")):
        hint = _after_error(result, rc, run_dir)
        if hint:
            print(f"problem: {hint}")
    if info["is_error"] or info["stop"] in ("interrupted", "timeout") or rc not in (0, None):
        print(f"to continue: lil-agent resume {run_dir}")
    print("network (Claude Code process; full log in .lil/network.log):")
    for line in proxy.summary() or ["(no connections)"]:
        print(f"  {line}")
    if proxy.blocked():
        print("  blocked attempts were refused; nothing reached those hosts")
    return 0 if rc == 0 and not info["is_error"] else 1


def run(zip_path, model=cborg.DEFAULT_MODEL, skill=None, runs_dir=None,
        max_turns=300, timeout_min=None, interactive=False):
    zip_path = Path(zip_path).expanduser().resolve()
    if not zip_path.is_file():
        sys.exit(f"lil-agent: not a file: {zip_path}")
    skill = Path(skill).expanduser().resolve() if skill else None
    if skill and not skill.is_dir():
        sys.exit(f"lil-agent: skill must be a directory: {skill}")
    runs_dir = Path(runs_dir or os.environ.get("LIL_RUNS", "~/lil_runs")).expanduser()

    entry, self_report = _check_model(model)
    if entry and not cborg.is_lbl_hosted(entry):
        print(f"warning: {model} is not LBL-hosted; data will leave LBL", file=sys.stderr)

    run_dir, skills = prepare(zip_path, model, skill, runs_dir)
    print(f"run directory: {run_dir}")
    if skills:
        print(f"skills installed: {', '.join(skills)}")
    (run_dir / ".lil" / "run.json").write_text(json.dumps({
        "zip": str(zip_path), "model": model, "cborg_model_entry": entry,
        "skills": skills, "attempts": []}, indent=2))

    skill_line = ("- Skills from the package: " +
                  ", ".join(f".claude/skills/{s}/SKILL.md" for s in skills) +
                  ". Read them before you start and follow them wherever the "
                  "instructions refer to them or they apply." if skills else "")
    prompt = PROMPT.format(zip_name=zip_path.name, memory_line=memory_line(),
                           skill_line=skill_line)
    return _execute(run_dir, model, prompt, entry, self_report, max_turns=max_turns,
                    timeout_min=timeout_min, interactive=interactive)


def resume(run_dir, message=None, model=None, max_turns=300, timeout_min=None,
           interactive=False):
    """Continue a stopped run's agent session where it left off."""
    run_dir = Path(run_dir).expanduser().resolve()
    path = run_dir / ".lil" / "run.json"
    if not path.exists():
        sys.exit(f"lil-agent: not a lil-agent run directory: {run_dir}")
    info = json.loads(path.read_text())
    session_id = info.get("session_id") or _session_from_transcript(run_dir)
    if not session_id:
        sys.exit("lil-agent: no agent session recorded for this run; start a new run")
    previous = (info.get("attempts") or [{}])[-1].get("model_provenance", {}).get("model") \
        or info.get("model", cborg.DEFAULT_MODEL)
    model = model or previous
    reason = info.get("stop") or "unknown reason"
    if info.get("is_error"):
        status = (info.get("attempts") or [{}])[-1].get("api_error_status")
        reason = f"API error {status}" if status else "an error"
    # Checked again: CBorg may have changed or removed the model since then.
    entry, self_report = _check_model(model, run_dir)
    if entry and not cborg.is_lbl_hosted(entry):
        print(f"warning: {model} is not LBL-hosted; data will leave LBL", file=sys.stderr)
    print(f"resuming session {session_id[:8]} in {run_dir}"
          + (f", switching model from {previous} to {model}" if model != previous else ""))
    message = message or RESUME_MESSAGE.format(reason=reason, memory_line=memory_line())
    return _execute(run_dir, model, message, entry, self_report, session_id=session_id,
                    max_turns=max_turns, timeout_min=timeout_min, interactive=interactive)
