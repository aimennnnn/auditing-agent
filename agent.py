"""Command-line entry point.

    python agent.py --repo https://github.com/owner/repo --output report.json

Clones the repository into a temporary folder, lets the agent audit it, and writes the
JSON report (plus a .trace.json file showing every tool call the agent made).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from agent_loop import ALL_TOOLS, MAX_STEPS, audit
from llm import GeminiLLM
from prompt import SYSTEM_PROMPT

DEFAULT_MODEL = "gemini-3.6-flash"
GITHUB_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def resolve_repo(value):
    """Return (what to clone, name for the report). A local git folder is also accepted, for testing."""
    value = value.strip()
    match = GITHUB_URL.match(value)
    if match:
        url = f"https://github.com/{match[1]}/{match[2]}"
        return url, url
    folder = Path(value).expanduser()
    if (folder / ".git").exists():
        return str(folder.resolve()), str(folder.resolve())
    raise ValueError(f"'{value}' is not a GitHub repository URL like https://github.com/owner/repo")


def clone(source, dest):
    """Clone into a temporary folder. The original repository is never touched."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}  # never wait for a password
    cmd = ["git", "clone", "--quiet", "--depth", "200", "--no-tags", "-c", "core.autocrlf=false", source, str(dest)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=300, env=env, check=False)
    except FileNotFoundError:
        raise RuntimeError("Git is not installed or not on PATH (get it from https://git-scm.com).") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Cloning took longer than 5 minutes, so the audit was stopped.") from None
    if result.returncode != 0:
        raise RuntimeError(f"Could not clone {source}. Is it public and spelled correctly?\n"
                           f"{result.stderr.strip()[-400:]}")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI agent that audits a GitHub repository for DevOps problems.")
    parser.add_argument("--repo", required=True, help="public GitHub URL, e.g. https://github.com/owner/repo")
    parser.add_argument("--output", required=True, help="where to write the JSON report, e.g. report.json")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS, help=f"max agent turns (default {MAX_STEPS})")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # odd characters must never crash a Windows console
    load_dotenv()

    try:
        source, repo_name = resolve_repo(args.repo)
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and paste your key into it.")
        model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
        llm = GeminiLLM(api_key, model, SYSTEM_PROMPT, ALL_TOOLS)

        print(f"Auditing {repo_name} with {model}\n")
        with tempfile.TemporaryDirectory(prefix="audit-", ignore_cleanup_errors=True) as tmp:
            repo_dir = Path(tmp) / "repo"
            clone(source, repo_dir)
            violations, trace = audit(llm, repo_dir, repo_name, max(args.max_steps, 1))

        trace_path = Path(args.output).with_suffix(".trace.json")
        write_json(args.output, {"repository": repo_name, "violations": violations})
        write_json(trace_path, trace)
    except KeyboardInterrupt:
        print("\nStopped by user.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"\n{len(violations)} violation(s):")
    for v in violations:
        where = f"{v['file']}:{v['line']}" if v["line"] else (v["file"] or "-")
        print(f"  {v['severity'].upper():<8} {v['rule_id']:<10} {where:<34} {v['description']}")
    print(f"\nReport -> {args.output}   Tool trace -> {trace_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
