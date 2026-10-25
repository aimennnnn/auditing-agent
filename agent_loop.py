"""The agent loop: Observe -> Reason -> Select tool -> Execute -> Interpret -> Report.

Gemini decides WHICH tools to call and WHY. This loop runs those tools, sends the results
back, and repeats until Gemini calls `finish` with its list of violations.
"""
from tools import TOOL_SCHEMAS, redact, run_tool

MAX_STEPS = 20
SEVERITIES = ["critical", "high", "medium", "low"]
CATEGORIES = ["repository_hygiene", "dependency_management", "code_quality", "testing", "ci",
              "security", "configuration", "collaboration", "build_release"]
FIELDS = ["rule_id", "category", "severity", "file", "line", "description", "evidence", "recommendation"]

FINISH_SCHEMA = {
    "name": "finish",
    "description": "Submit the final audit report. Call exactly once, after checking every area. "
                   "Use an empty list if nothing is wrong.",
    "parameters": {
        "type": "object",
        "properties": {"violations": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "Rule id from the list, e.g. SEC-001."},
                "category": {"type": "string", "enum": CATEGORIES},
                "severity": {"type": "string", "enum": SEVERITIES},
                "file": {"type": "string", "description": "Repo-relative path, or \"\" if not about one existing file."},
                "line": {"type": "integer", "description": "1-based line number, or 0 if not about one line."},
                "description": {"type": "string", "description": "One sentence naming the problem."},
                "evidence": {"type": "string", "description": "What you observed and where. Never a secret value."},
                "recommendation": {"type": "string", "description": "A specific fix."},
            },
            "required": FIELDS,
        }}},
        "required": ["violations"],
    },
}
ALL_TOOLS = TOOL_SCHEMAS + [FINISH_SCHEMA]

START = ("Audit the repository {repo}. It is already cloned and read-only. You have at most {steps} "
         "turns, and each turn can contain several tool calls. Begin.")
NUDGE = "Continue the audit: call the next tools, or call finish if every area is checked."
LAST_TURN = "Your turn budget is used up. Call finish now with every violation you verified (or an empty list)."


def audit(llm, repo_dir, repo_name, max_steps=MAX_STEPS, log=print):
    """Run the agent until it calls finish. Returns (violations, trace)."""
    trace = []
    calls = llm.send(text=START.format(repo=repo_name, steps=max_steps))
    for step in range(1, max_steps + 2):  # the extra turn is the forced `finish`
        results = []
        for call in calls:
            args = call.get("args") or {}
            if call["name"] == "finish":
                if isinstance(args.get("violations"), list):
                    violations = clean_violations(args["violations"])
                    log(f"[{step:>2}] finish -> {len(violations)} violation(s)")
                    return violations, trace
                output = "Error: finish needs a 'violations' list."
            else:
                output = run_tool(call["name"], args, repo_dir)       # Execute
            shown = {k: redact(v) if isinstance(v, str) else v for k, v in args.items() if k != "reason"}
            reason = redact(args.get("reason", "-"))
            summary = redact((output.splitlines() or [""])[0])[:110]
            log(f"[{step:>2}] {call['name']}({', '.join(f'{k}={v!r}' for k, v in shown.items())})\n"
                f"     why: {reason}\n     ->  {summary}")
            trace.append({"step": step, "tool": call["name"], "args": shown, "reason": reason,
                          "result": redact(output[:2000])})
            results.append((call, output))
        if step > max_steps:
            break
        last = step == max_steps
        calls = llm.send(text=LAST_TURN if last else (None if results else NUDGE),   # Interpret: Gemini reads
                         tool_results=results, force_finish=last)                    # the results, picks next
    raise RuntimeError("The model never called finish, so no report was produced.")


def clean_violations(raw):
    """Validate, normalise and de-duplicate the model's findings, and mask any secret values."""
    cleaned, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file") or "").strip().replace("\\", "/").removeprefix("./")
        file = None if file.lower() in ("", "null", "none", "n/a") else file
        severity = str(item.get("severity") or "").strip().lower()
        violation = {
            "rule_id": str(item.get("rule_id") or "GEN-001").strip().upper(),
            "category": str(item.get("category") or "").strip().lower(),
            "severity": severity if severity in SEVERITIES else "medium",
            "file": file,
            "line": _positive_int(item.get("line")) if file else None,
            "description": redact(str(item.get("description") or "").strip()),
            "evidence": redact(str(item.get("evidence") or "").strip()),
            "recommendation": redact(str(item.get("recommendation") or "").strip()),
        }
        key = (violation["rule_id"], violation["file"], violation["line"])
        if key not in seen:  # duplicates earn nothing and hurt precision
            seen.add(key)
            cleaned.append(violation)
    return sorted(cleaned, key=lambda v: SEVERITIES.index(v["severity"]))


def _positive_int(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
