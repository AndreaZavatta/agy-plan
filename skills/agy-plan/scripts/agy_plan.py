#!/usr/bin/env python3
"""Run a read-only Antigravity (agy) planning session and save the plan as Markdown.

Prints a single JSON summary on stdout and never the plan itself, so the calling
agent spends no tokens on it until the plan is approved.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

DEFAULT_MODEL = os.environ.get("AGY_PLAN_MODEL", "gemini-4-argon-high")
DEFAULT_TIMEOUT_SECONDS = 1800
MAX_RETRIES = 2
PLANS_DIR = ".agy-plans"
START = "<<<AGY_PLAN_START>>>"
END = "<<<AGY_PLAN_END>>>"
PROMPT_TEMPLATE = Path(__file__).resolve().parent.parent / "references" / "planner-prompt.md"
# --mode plan only guides the model and does not stop it from writing to the project,
# so read-only is enforced with session-scoped deny rules.
READ_ONLY_DENY = ["write_file(*)", "command(*)", "mcp(*)"]
# agy's read_file rules match exact paths and directories but not globs (tested with agy
# 1.5.17), so every secrets file found in the project gets a rule of its own.
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
SKIPPED_DIRS = {".git", "node_modules"}

# agy reports SUCCESS even when a denied tool call cut the turn short, so a run only
# counts once the end marker is present. These prompts resume such a run.
RETRY_PROMPT = (
    "Your previous turn ended before the complete plan was delivered. The terminal and "
    "file writes are blocked. Using only your built-in tools for reading and searching, "
    f"finish the work and send the complete plan in your message, between {START} and {END}."
)
# agy silently starts a new conversation when the one to resume no longer exists, so a
# refinement repeats the planner prompt and stands on its own either way.
REFINE_PROMPT = (
    "\n\n## Revision\n\nA human reviewed an earlier version of this plan. The current version "
    "is below and may include their direct edits, which you must keep. Apply their feedback, "
    "checking the code again where it matters, and send the complete updated plan with the "
    "rules, sections and output format above."
    "\n\n<feedback>\n{feedback}\n</feedback>\n\n<current_plan>\n{plan}\n</current_plan>"
)


class PlanError(Exception):
    def __init__(self, message, conversation_id=None):
        super().__init__(message)
        self.conversation_id = conversation_id


def find_env_files(project):
    """Return the project's .env files, such as .env and backend/.env.local, but not templates like .env.example."""
    found = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
        found += [Path(root, name).relative_to(project).as_posix() for name in files
                  if (name == ".env" or name.startswith(".env.")) and name not in ENV_TEMPLATES]
    return sorted(found)


def read_only_settings(blocked_files):
    deny = READ_ONLY_DENY + [f"read_file({path})" for path in blocked_files]
    return json.dumps({"permissions": {"deny": deny}})


def run_agy(prompt, project, model, timeout, settings, conversation_id=None):
    cmd = ["agy", "-p", prompt, "--mode", "plan", "--settings", settings, "--model", model,
           "--output-format", "json", "--print-timeout", f"{timeout}s"]
    if conversation_id:
        cmd += ["--conversation", conversation_id]
    try:
        # The margin lets agy report its own timeout before this one kills it.
        proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=timeout + 60)
    except FileNotFoundError:
        raise PlanError("agy not found on PATH: install the Antigravity CLI") from None
    except subprocess.TimeoutExpired:
        raise PlanError(f"agy did not finish within {timeout}s", conversation_id) from None
    if proc.returncode != 0:
        raise PlanError(f"agy exited with {proc.returncode}: {proc.stderr.strip()[:1000]}", conversation_id)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise PlanError(f"agy returned invalid JSON: {proc.stdout[:300]!r}", conversation_id) from None
    if result.get("status") != "SUCCESS":
        raise PlanError(f"agy status {result.get('status')}: {proc.stderr.strip()[:1000]}",
                        result.get("conversation_id") or conversation_id)
    return result


def extract_plan(response):
    end = response.rfind(END)
    start = response.rfind(START, 0, end) if end != -1 else -1
    if start == -1:
        return None
    return response[start + len(START):end].strip() or None


def relativize_links(plan, project):
    """Turn file:// links into repo-relative ones: shorter to read, and portable."""
    root = str(project)
    for prefix in (root, urllib.parse.quote(root)):
        plan = plan.replace(f"file://{prefix}/", "")
    return plan


def plan_session(prompt, project, model, timeout, conversation_id=None):
    """Run agy until it delivers a complete plan, resuming a cut-off turn up to MAX_RETRIES times."""
    usage, denied, total_seconds = {}, [], 0.0
    # Found once, so retries of the same session block the same files.
    blocked_files = find_env_files(project)
    settings = read_only_settings(blocked_files)
    for attempt in range(1, MAX_RETRIES + 2):
        result = run_agy(prompt, project, model, timeout, settings, conversation_id)
        conversation_id = result.get("conversation_id") or conversation_id
        total_seconds += result.get("duration_seconds") or 0
        denied += result.get("denied_actions") or []
        for key, value in (result.get("usage") or {}).items():
            usage[key] = usage.get(key, 0) + value
        plan = extract_plan(result.get("response") or "")
        if plan:
            return {"plan": relativize_links(plan, project), "conversation_id": conversation_id,
                    "usage": usage, "denied_actions": denied, "attempts": attempt,
                    "duration_seconds": round(total_seconds, 1), "blocked_files": blocked_files}
        prompt = RETRY_PROMPT
    raise PlanError(f"no complete plan after {MAX_RETRIES + 1} attempts (denied actions: {denied})", conversation_id)


def git_status(project):
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def slugify(text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_text.lower())
    return "-".join(words[:8]) or "plan"


def parse_plan_file(path):
    match = re.match(r"---\n(.*?)\n---\n+(.*)", path.read_text(encoding="utf-8"), re.DOTALL)
    if not match:
        raise PlanError(f"{path} has no agy-plan front matter")
    meta = {}
    for line in filter(str.strip, match.group(1).splitlines()):
        key, _, value = line.partition(": ")
        try:
            meta[key] = json.loads(value)
        except json.JSONDecodeError:
            raise PlanError(f"{path}: front matter line {line!r} is not 'key: <JSON value>'") from None
    # "project" is optional: plans written before it was recorded lack it.
    missing = [key for key in ("task", "model", "conversation_id") if key not in meta]
    if missing:
        raise PlanError(f"{path}: front matter is missing {', '.join(missing)}")
    return meta, match.group(2)


def next_version_path(path):
    """Return the first free version after path, so refining an older version never overwrites a newer one."""
    match = re.fullmatch(r"(.*)-v(\d+)", path.stem)
    stem, version = (match.group(1), int(match.group(2))) if match else (path.stem, 1)
    candidate = path.with_name(f"{stem}-v{version + 1}.md")
    while candidate.exists():
        version += 1
        candidate = path.with_name(f"{stem}-v{version + 1}.md")
    return candidate


def write_plan(path, body, task, model, conversation_id, project):
    meta = {"task": task, "model": model, "conversation_id": conversation_id, "project": str(project),
            "created": dt.datetime.now().isoformat(timespec="seconds")}
    front_matter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in meta.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front_matter}\n---\n\n{body}\n", encoding="utf-8")


def planner_prompt(task):
    return PROMPT_TEMPLATE.read_text(encoding="utf-8").replace("{{TASK}}", task)


def build_new_plan(args):
    task = sys.stdin.read() if args.task_file == "-" else Path(args.task_file).read_text(encoding="utf-8")
    task = task.strip()
    if not task:
        raise PlanError("the task is empty")
    project = Path(args.project or ".").resolve()
    prompt = planner_prompt(task)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = project / PLANS_DIR / f"{stamp}-{slugify(task)}.md"
    return project, prompt, path, task, args.model or DEFAULT_MODEL, None


def build_refinement(args):
    source = Path(args.refine).resolve()
    meta, body = parse_plan_file(source)
    feedback = sys.stdin.read().strip() if args.feedback == "-" else args.feedback
    if not feedback:
        raise PlanError("the feedback is empty")
    # The recorded project survives moving the plan file; older plans sit in <project>/.agy-plans/.
    project = Path(args.project or meta.get("project") or source.parent.parent).resolve()
    if not project.is_dir():
        raise PlanError(f"project {project} not found: pass --project")
    prompt = planner_prompt(meta["task"]) + REFINE_PROMPT.format(feedback=feedback, plan=body)
    return (project, prompt, next_version_path(source), meta["task"],
            args.model or meta["model"], meta["conversation_id"])


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-file", help="file holding the task, or - for stdin")
    parser.add_argument("--project", help="project to plan for (default: current directory, or the one "
                        "recorded in the plan with --refine)")
    parser.add_argument("--refine", metavar="PLAN", help="plan file to revise in its original agy conversation")
    parser.add_argument("--feedback", help="review feedback, or - for stdin; required with --refine")
    parser.add_argument("--model", help=f"agy model ID (default: {DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="seconds per agy run")
    args = parser.parse_args(argv)
    if bool(args.refine) == bool(args.task_file):
        parser.error("pass exactly one of --task-file and --refine")
    if bool(args.refine) != bool(args.feedback):
        parser.error("--refine and --feedback go together")
    return args


def main(argv=None):
    args = parse_args(argv)
    conversation_id = None
    try:
        project, prompt, path, task, model, conversation_id = (
            build_refinement(args) if args.refine else build_new_plan(args))
        status_before = git_status(project)
        session = plan_session(prompt, project, model, args.timeout, conversation_id)
        # Compared before the plan is saved, so the plan file itself never counts as a change.
        worktree_changed = status_before is not None and git_status(project) != status_before
        write_plan(path, session.pop("plan"), task, model, session["conversation_id"], project)
        if args.refine:
            session["resumed"] = session["conversation_id"] == conversation_id
    except (PlanError, OSError) as error:
        conversation_id = getattr(error, "conversation_id", None) or conversation_id
        print(json.dumps({"ok": False, "error": str(error), "conversation_id": conversation_id}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "plan_path": str(path), "model": model,
                      "worktree_changed": worktree_changed, **session}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
