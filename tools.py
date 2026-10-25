"""Inspection tools the agent can call.

Every tool is READ-ONLY: nothing here edits, commits, installs or runs code from the
audited repository. Each tool returns plain text whose first line is a short summary.
Outputs are size-limited so the conversation with the LLM stays small.
"""
import fnmatch
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

MAX_CHARS = 12_000
MAX_LINES_PER_READ = 200
MAX_MATCHES = 60


# ---------------------------------------------------------------- helpers

def _git(repo, *args, timeout=60):
    """Run a read-only git command inside the repository and return its output."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def _tracked_files(repo):
    """Files committed to the repository: exactly what a fresh clone contains."""
    return [p for p in _git(repo, "ls-files", "-z").split("\0") if p]


def _safe_path(repo, path):
    """Turn a repo-relative path into a real path, refusing anything outside the repo."""
    root = Path(repo).resolve()
    clean = str(path).strip().replace("\\", "/").removeprefix("./").lstrip("/")
    full = (root / clean).resolve()
    if not full.is_relative_to(root):
        raise ValueError(f"'{path}' is outside the repository")
    return full


def _is_text(full):
    with open(full, "rb") as f:
        return b"\0" not in f.read(8192)


def _compile(pattern):
    """Case-insensitive regex. An invalid regex is searched as plain text instead."""
    try:
        return re.compile(pattern, re.IGNORECASE), ""
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE), " (invalid regex, searched as plain text)"


def _clip(text):
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n[... output truncated]"


# ---------------------------------------------------------------- the tools

def list_files(repo, directory=""):
    """List committed files, optionally under one folder. Big repos get a folder summary."""
    prefix = str(directory or "").strip().replace("\\", "/").strip("/")
    files = [f for f in _tracked_files(repo) if not prefix or f == prefix or f.startswith(prefix + "/")]
    where = f" under '{prefix}/'" if prefix else ""
    if not files:
        return f"No committed files{where}."
    if len(files) <= 300:
        return f"{len(files)} committed files{where}:\n" + "\n".join(files)
    depth = prefix.count("/") + 1 if prefix else 0
    groups = Counter()
    for f in files:
        parts = f.split("/")
        groups["/".join(parts[:depth + 1]) + ("/" if len(parts) > depth + 1 else "")] += 1
    lines = [f"{name} ({n} files)" if name.endswith("/") else name for name, n in sorted(groups.items())]
    return _clip(f"{len(files)} committed files{where}, too many to list. Folders shown; "
                 "call list_files with a directory to look inside:\n" + "\n".join(lines))


def read_file(repo, path, start_line=1):
    """Show a file with line numbers, up to 200 lines per call."""
    full = _safe_path(repo, path)
    if not full.exists():
        return f"File not found: {path} (use list_files to see what exists)."
    if full.is_dir():
        return f"{path} is a folder (use list_files with directory='{path}')."
    if not _is_text(full):
        return f"{path} is a binary file ({full.stat().st_size} bytes); contents not shown."
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return f"{path} is empty (0 lines)."
    start = max(int(start_line or 1), 1)
    if start > len(lines):
        return f"{path} has only {len(lines)} lines."
    shown, size, end = [], 0, start - 1
    while end < len(lines) and end - start + 1 < MAX_LINES_PER_READ and size < MAX_CHARS:
        row = f"{end + 1}: {lines[end][:300]}"
        shown.append(row)
        size += len(row)
        end += 1
    more = f"\n[... more below: call read_file with start_line={end + 1}]" if end < len(lines) else ""
    return f"{path} (lines {start}-{end} of {len(lines)})\n" + "\n".join(shown) + more


def search_code(repo, pattern, file_glob=""):
    """Case-insensitive regex search across committed text files, like grep -n."""
    regex, note = _compile(pattern)
    glob = str(file_glob or "").removeprefix("**/")
    hits, total = [], 0
    for rel in _tracked_files(repo):
        if glob and not fnmatch.fnmatch(rel, glob):
            continue
        full = Path(repo) / rel
        if full.is_symlink() or not full.is_file() or full.stat().st_size > 1_000_000 or not _is_text(full):
            continue
        for number, line in enumerate(full.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                total += 1
                if len(hits) < MAX_MATCHES:
                    hits.append(f"{rel}:{number}: {line.strip()[:200]}")
    if not hits:
        return f"No matches for /{pattern}/{note}."
    more = f" (showing first {MAX_MATCHES})" if total > MAX_MATCHES else ""
    return _clip(f"{total} matches for /{pattern}/{note}{more}:\n" + "\n".join(hits))


def git_log(repo, max_count=20):
    """Recent commits with the files each one changed, plus the branch name."""
    n = min(max(int(max_count or 20), 1), 100)
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    total = _git(repo, "rev-list", "--count", "HEAD").strip()
    log = _git(repo, "log", f"-n{n}", "--date=short", "--name-status", "--format=%n%h %ad %an: %s")
    return _clip(f"Default branch: {branch} | commits available: {total}\n{log.strip()}")


def search_history(repo, pattern):
    """Find lines matching a pattern that were ADDED or REMOVED in past commits.

    This catches secrets that were committed and later deleted: they are still in history.
    """
    regex, note = _compile(pattern)
    log = _git(repo, "log", "-p", "-n200", "--unified=0", "--no-color", "--no-ext-diff",
               "--date=short", "--format=@@COMMIT %h %ad %s", timeout=120)
    hits, commit, path = [], "", ""
    for line in log.splitlines():
        if line.startswith("@@COMMIT "):
            commit = line[9:]
        elif line.startswith(("--- a/", "+++ b/")):
            path = line[6:]
        elif line[:1] in ("+", "-") and not line.startswith(("+++", "---")) and regex.search(line[1:]):
            change = "added" if line[0] == "+" else "removed"
            hits.append(f"{commit} | {path} | {change}: {line[1:].strip()[:200]}")
            if len(hits) >= 40:
                break
    if not hits:
        return f"No added/removed lines matching /{pattern}/{note} in the last 200 commits."
    return _clip(f"{len(hits)} matching changes in git history for /{pattern}/{note}:\n" + "\n".join(hits))


def _ruff_binary():
    """Find the ruff executable installed next to this Python (or on PATH)."""
    folder = Path(sys.executable).parent
    for candidate in (folder / "ruff", folder / "ruff.exe", folder / "Scripts" / "ruff.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ruff")


def run_ruff(repo):
    """Static check for serious Python errors only (syntax errors, undefined names).

    Ruff reads files; it never imports or runs the repository's code.
    """
    ruff = _ruff_binary()
    if not ruff:
        return "ruff is not installed, so this check is unavailable."
    cmd = [ruff, "check", ".", "--isolated", "--no-cache", "--exit-zero",
           "--select", "E9,F63,F7,F82", "--output-format", "concise"]
    result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120, check=False)
    output = (result.stdout + result.stderr).strip()
    count = sum(1 for line in output.splitlines() if re.match(r".+:\d+:\d+: ", line))
    return _clip(f"ruff found {count} serious error(s) (syntax errors, undefined names, invalid comparisons):\n"
                 + (output or "No issues."))


TOOLS = {f.__name__: f for f in (list_files, read_file, search_code, git_log, search_history, run_ruff)}


def run_tool(name, args, repo):
    """Run one tool call. Problems come back as text so the agent can react instead of crashing."""
    func = TOOLS.get(name)
    if func is None:
        return f"Error: unknown tool '{name}'. Available tools: {', '.join(TOOLS)}."
    args = {k: v for k, v in (args or {}).items() if k != "reason"}
    try:
        return func(repo, **args)
    except subprocess.TimeoutExpired:
        return f"Error: {name} timed out."
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as e:
        return f"Error in {name}: {e}"  # bad arguments, unreadable file, git error...


# ---------------------------------------------------------------- tool descriptions for the LLM

REASON = {"type": "string", "description": "One short sentence: what you noticed and why you call this tool."}


def _schema(name, description, properties=None, required=()):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": {"reason": REASON, **(properties or {})},
                           "required": ["reason", *required]}}


TOOL_SCHEMAS = [
    _schema("list_files", "List the files committed to the repository. Start here. "
            "Pass a directory to look inside one folder.",
            {"directory": {"type": "string", "description": "Optional folder, e.g. '.github/workflows'."}}),
    _schema("read_file", "Read a file with line numbers (200 lines per call).",
            {"path": {"type": "string", "description": "Repo-relative path, e.g. 'src/config.py'."},
             "start_line": {"type": "integer", "description": "First line to show (default 1)."}},
            ["path"]),
    _schema("search_code", "Case-insensitive regex search over all committed text files. "
            "Returns path:line: text for each match.",
            {"pattern": {"type": "string", "description": "Python regex, e.g. '(api_key|secret|password)\\s*[:=]'."},
             "file_glob": {"type": "string", "description": "Optional file filter, e.g. '*.py' or '.github/*'."}},
            ["pattern"]),
    _schema("git_log", "Recent commits (hash, date, author, message, changed files) and the default branch name.",
            {"max_count": {"type": "integer", "description": "Number of commits (default 20, max 100)."}}),
    _schema("search_history", "Search the last 200 commits for added or removed lines matching a regex. "
            "Finds secrets that were deleted but are still in git history.",
            {"pattern": {"type": "string", "description": "Python regex, e.g. '(password|token|secret|api_key)'."}},
            ["pattern"]),
    _schema("run_ruff", "Static analysis for serious Python errors only (syntax errors, undefined names). "
            "Does not run the code."),
]


# ---------------------------------------------------------------- secret masking

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                    # AWS access key id
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),     # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),                       # Google API keys
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),                # Slack tokens
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"),  # Stripe keys
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),                        # OpenAI-style keys
]
URL_PASSWORD = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)")
ASSIGNED_SECRET = re.compile(
    r"(?i)((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
    r"[\w.-]*[\"']?\s*[:=]\s*[\"']?)([^\s\"'`,;()]{8,})")
NOT_A_SECRET = re.compile(
    r"(?i)^(os\.|getenv|environ|process\.env|settings\.|config\.|self\.|\$|\{\{|<|\[REDACTED\]"
    r"|your|example|changeme|placeholder|xxx)")


def _mask_assignment(match):
    value = match.group(2)
    looks_secret = (len(value) >= 8 and any(c.isdigit() for c in value)) or len(value) >= 16
    if NOT_A_SECRET.match(value) or not looks_secret:
        return match.group(0)
    return match.group(1) + "[REDACTED]"


def redact(text):
    """Mask values that look like real secrets (used for the console, the trace and the report)."""
    text = str(text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = URL_PASSWORD.sub(r"\1[REDACTED]\3", text)
    return ASSIGNED_SECRET.sub(_mask_assignment, text)
