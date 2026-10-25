# DevOps Repository Auditing Agent

![CI](https://github.com/aimennnnn/auditing-agent/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![LLM](https://img.shields.io/badge/LLM-Google%20Gemini-4285F4)

*MLOps & Production AI Engineering · Assignment 2*

---

## 1. Summary

You give the agent a link to a public GitHub repository. It downloads the repository, looks
around **by itself**, and writes a `report.json` file listing the DevOps problems it found.
Every problem comes with the file and line where it was found, the evidence, and how to fix it.

Main points:

- **It is a real agent, not a checklist.** Google Gemini decides which tool to use next and
  explains why. What it opens depends on what it has already seen.
- **No agent frameworks.** The loop is plain Python. No LangChain, LangGraph or n8n. The only AI
  library is Google's basic SDK (`google-genai`), with its automatic tool calling turned off so
  that our own loop runs the tools.
- **Read-only.** It never changes the repository it inspects and never runs its code.
- **Secrets stay hidden.** Anything that looks like a password or key is masked before it is
  printed or saved.
- **Tested.** 18 automated tests run without an API key, and GitHub Actions runs them on every push.

---

## 2. How it works

One run, from start to finish:

```
   YOU             AGENT (our code)                  GEMINI               TOOLS
                 agent.py, agent_loop.py          (via llm.py)          tools.py
    |                       |                           |                   |
    | agent.py --repo <url> |                           |                   |
    |---------------------->|                           |                   |
    |                       | clone into temp folder    |                   |
    |                       |                           |                   |
    |                       | task + list of tools      |                   |
    |                       |-------------------------->|                   |
    |                       |                           |                   |
    |                       | call list_files + why     |                   |
    |                       |<--------------------------|                   |
    |                       |                           | run list_files    |
    |                       |---------------------------------------------->|
    |                       |                           | 10 files          |
    |                       |<----------------------------------------------|
    |                       | send back the result      |                   |
    |                       |-------------------------->|                   |
    |                       |                           |                   |
    |                       |  ... same loop with other tools ...           |
    |                       |                           |                   |
    |                       | call finish(violations)   |                   |
    |                       |<--------------------------|                   |
    |                       | check, remove repeats,    |                   |
    |                       | hide secrets              |                   |
    |                       |                           |                   |
    | report.json + trace   |                           |                   |
    |<----------------------|                           |                   |
    |                       |                           |                   |
```

### The 6 steps of the loop

| Step | What happens |
|---|---|
| **1. Observe** | The agent starts by looking at the file list and the git history. |
| **2. Reason** | Gemini thinks about what it has seen, e.g. "there is a CI file, I should check it". |
| **3. Select tool** | Gemini picks the next tool (or several at once) and writes a short `reason` for each. |
| **4. Execute** | Our code runs the tool on the downloaded copy. Tools can only read, never change. |
| **5. Interpret** | The result goes back to Gemini, which decides what to check next. A search hit leads to reading that file; a commit called "Remove .env file" leads to searching the old history. |
| **6. Report** | When every area is checked, Gemini calls `finish` with its list of problems. Our code checks each one, removes duplicates, hides secrets and saves the JSON. |

The agent has a limit of 20 turns. If it reaches the limit, the last turn forces it to call
`finish`, so a run always ends with a report.

### The tools

| Tool | What it does |
|---|---|
| `list_files(directory)` | Lists the files committed to the repo. Big repos are shown folder by folder. |
| `read_file(path, start_line)` | Shows a file with line numbers, 200 lines at a time. |
| `search_code(pattern, file_glob)` | Searches all committed files with a regex, like `grep`. |
| `git_log(max_count)` | Shows recent commits, the files they changed, and the branch name. |
| `search_history(pattern)` | Searches lines that were added or **deleted** in old commits. This finds secrets that were removed but are still in the git history. |
| `run_ruff()` | Finds syntax errors and undefined names in Python code without running it. |
| `finish(violations)` | Gemini hands in the final list of problems. |

---

## 3. What it checks

| Area | Rule IDs (what each one means) |
|---|---|
| **Repository hygiene** | `HYG-001` no README<br>`HYG-002` missing or weak `.gitignore`<br>`HYG-003` junk files committed (`__pycache__`, `.pyc`, logs, `venv`, `node_modules`) |
| **Dependencies** | `DEP-001` no dependency file<br>`DEP-002` versions not pinned<br>`DEP-003` files don't match (e.g. code imports a package that isn't listed)<br>`DEP-004` missing lockfile when the tool needs one (Poetry, Pipenv, npm) |
| **Code quality** | `QUAL-001` no linter or formatter set up<br>`QUAL-002` code errors found by ruff<br>`QUAL-003` a clear maintainability problem |
| **Testing** | `TEST-001` no tests<br>`TEST-002` tests never run in CI<br>`TEST-003` a test that obviously can't pass |
| **CI (GitHub Actions)** | `CI-001` wrong triggers (e.g. watches `master` when the branch is `main`)<br>`CI-002` a check that can never fail (`\|\| true`, `continue-on-error: true`)<br>`CI-003` action pinned to a moving branch (`@main`) |
| **Security** | `SEC-001` password or key written in the code<br>`SEC-002` secret file committed (`.env`, `.pem`)<br>`SEC-003` secret still in git history<br>`SEC-004` workflow has more permissions than it needs<br>`SEC-005` secrets exposed in CI logs or given to untrusted code |
| **Configuration** | `CONF-001` path that only works on one computer (`C:\Users\...`)<br>`CONF-002` personal or IDE settings committed<br>`CONF-003` no separation between environments (e.g. `DEBUG = True` everywhere) |
| **Collaboration** | `COLLAB-001` pull requests merged automatically without checks<br>`COLLAB-002` no review guidance where the repo clearly needs it |
| **Build and release** | `BUILD-001` project is never built in CI<br>`BUILD-002` release or deploy runs without tests first<br>`BUILD-003` Docker base image uses `:latest` |

### Severity

| Severity | Used for |
|---|---|
| `critical` | Real-looking passwords or keys in the current code; CI that leaks secrets |
| `high` | Secrets in git history, committed `.env` files, CI tests that can't fail, `write-all` permissions |
| `medium` | No tests, no CI, unpinned dependencies, junk files, machine-specific paths, code errors |
| `low` | Small documentation gaps, no linter, actions pinned to a branch |

### What it does **not** report (to avoid false alarms)

- Placeholder values like `your-key-here` in `.env.example`, or `${{ secrets.NAME }}` in workflows.
- A "missing" file when an equivalent exists (`pyproject.toml` instead of `requirements.txt`,
  lint settings inside `pyproject.toml`, any CI system, `README.rst`).
- Branch protection and other GitHub settings, because they are not visible in the files.
- Anything it did not actually see with its tools.

---

## 4. Project structure

```
auditing-agent/
├── agent.py                  # start here: reads --repo and --output, clones, saves the report
├── agent_loop.py             # the agent loop, the finish tool, and report cleanup
├── llm.py                    # talks to Gemini: tool list, conversation history, retries
├── prompt.py                 # instructions for Gemini: what to check, severity, rule IDs
├── tools.py                  # the 6 read-only tools + secret masking
├── tests/                    # 18 automated tests (fake Gemini, no API key needed)
│   ├── conftest.py           #   shared helpers: tiny test repo + fake Gemini
│   ├── test_tools.py
│   ├── test_agent.py
│   └── test_llm.py
├── sample_report.json        # real output from running the agent on the demo repo
├── sample_report.trace.json  # every tool call the agent made for that report
├── requirements.txt          # dependencies (pinned versions)
├── .env.example              # copy to .env and add your Gemini key
├── .github/workflows/ci.yml  # runs ruff + pytest on every push and pull request
├── pytest.ini
└── ruff.toml
```

---

## 5. Setup

You need **Python 3.10+**, **Git**, and a free Gemini API key from
<https://aistudio.google.com/apikey>.

**Step 1: Download the project**

```bash
git clone https://github.com/aimennnnn/auditing-agent.git
cd auditing-agent
```

**Step 2: Create and turn on a virtual environment**

```bash
python -m venv .venv
```

- Windows (PowerShell): `.venv\Scripts\Activate.ps1`
  (if Windows blocks it, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once)
- Mac / Linux: `source .venv/bin/activate`

**Step 3: Install the dependencies**

```bash
pip install -r requirements.txt
```

**Step 4: Add your API key**

- Windows: `copy .env.example .env`
- Mac / Linux: `cp .env.example .env`

Open `.env` and replace `paste-your-key-here` with your key. `GEMINI_MODEL` is optional
(default `gemini-3.6-flash`; `gemini-3.8-flash` also works on the free tier).

---

## 6. How to run

```bash
python agent.py --repo https://github.com/owner/repo --output report.json
```

You will see every step live. Each step shows the tool, **why** Gemini chose it, and the
first line of the result:

```
Auditing https://github.com/aimennnnn/demo-target with gemini-3.6-flash

[ 1] list_files()
     why: Initial exploration of repository structure.
     ->  10 committed files:
[ 1] git_log()
     why: Initial check of commit history and default branch name.
     ->  Default branch: main | commits available: 4
...
[ 5] finish -> 12 violation(s)

12 violation(s):
  CRITICAL SEC-001    config.py:4                 Hardcoded API key credential in configuration file.
  HIGH     CI-002     .github/workflows/ci.yml:20 Test step in CI uses an ineffective gate that swallows test failures.
  ...
Report -> report.json   Tool trace -> report.trace.json
```

It creates two files:

- `report.json`: the report (format below).
- `report.trace.json`: every tool call with Gemini's reason and a preview of the result.

| Exit code | Meaning |
|---|---|
| `0` | The inspection finished. An empty `violations` list means nothing was found. |
| `1` | Something stopped it (bad URL, clone failed, API problem). A clear message is printed. |

Options: `--max-steps 20` changes the turn limit. `--repo` also accepts a local git folder,
which is handy for testing.

---

## 7. Output format

```json
{
  "repository": "https://github.com/aimennnnn/demo-target",
  "violations": [
    {
      "rule_id": "SEC-001",
      "category": "security",
      "severity": "critical",
      "file": "config.py",
      "line": 4,
      "description": "Hardcoded API key credential in configuration file.",
      "evidence": "config.py line 4 assigns an API key literal to API_KEY.",
      "recommendation": "Load the key from an environment variable and rotate the exposed key."
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `rule_id` | ID from the table in section 3 |
| `category` | One of the 9 areas |
| `severity` | `critical`, `high`, `medium` or `low` |
| `file`, `line` | Where the problem is (`null` if it isn't about one file or line) |
| `description` | The problem in one sentence |
| `evidence` | What the agent saw and where. Secret values are never written. |
| `recommendation` | How to fix it |

---

## 8. Demo

The demo repo **[demo-target](https://github.com/aimennnnn/demo-target)** is a small fake shop
API with 11 problems planted on purpose, plus one trap that should **not** be reported.

```bash
python agent.py --repo https://github.com/aimennnnn/demo-target --output report.json
```

| # | Planted problem | Where | Expected rule |
|---|---|---|---|
| 1 | API key written in the code | `config.py` line 4 | `SEC-001` |
| 2 | `.env` with passwords committed, then deleted (still in git history) | commit "Remove .env file" | `SEC-003` |
| 3 | `pytest \|\| true`, so failing tests never fail CI | `ci.yml` line 20 | `CI-002` |
| 4 | `permissions: write-all` | `ci.yml` line 7 | `SEC-004` |
| 5 | CI watches `master`, but the branch is `main` | `ci.yml` line 5 | `CI-001` |
| 6 | No versions in requirements | `requirements.txt` | `DEP-002` |
| 7 | `yaml` is imported but not in requirements | `app.py` line 5 | `DEP-003` |
| 8 | `__pycache__/*.pyc` and `server.log` committed | repo root | `HYG-003` |
| 9 | `.gitignore` only ignores `.DS_Store` | `.gitignore` | `HYG-002` |
| 10 | Windows path `C:\Users\ahmed\...` | `config.py` line 5 | `CONF-001` |
| 11 | `DEBUG = True`, also on the production server | `config.py` line 6 | `CONF-003` |
| Trap | `.env.example` only has placeholder values | `.env.example` | must **not** be reported |

**Result** (see `sample_report.json`): all 11 found, the trap ignored, and one extra true
finding (`QUAL-001`, no linter set up). Gemini may word things a little differently each run,
but the same problems come up.

---

## 9. Tests

```bash
pytest
```

The tests use a fake Gemini and tiny git repos made on the fly, so they need **no API key and no
internet**.

| File | What it tests |
|---|---|
| `test_tools.py` | Every tool; `../` paths are blocked; secrets are masked; tools never change the repo |
| `test_agent.py` | The loop runs the tools Gemini picks (several at once too); a stuck model gets a nudge; `finish` is forced at the step limit; tool errors go back to Gemini; the report is cleaned and de-duplicated; the command line handles bad URLs and writes a valid report |
| `test_llm.py` | Tool results are sent back with matching IDs; rate limits are retried; a used-up quota or a bad key gives a clear error |

GitHub Actions (`.github/workflows/ci.yml`) runs `ruff` and `pytest` on every push and pull request.

---

## 10. Safety and error handling

- The repo is cloned into a **temporary folder** that is deleted afterwards. The original is never touched.
- **No code from the repo is run**: no installs, no tests, no imports. `run_ruff` only reads files.
- Tools **cannot read outside** the cloned folder (`../` tricks are blocked).
- **Secrets are masked** in the console, the trace and the report.
- Git never waits for a password, and every command has a **time limit**.
- Tool output is **size-limited**, so huge files don't flood Gemini.
- If a tool fails, the error goes back to Gemini as text, so the run **keeps going** instead of crashing.
- If Gemini is busy or rate-limited (429), it **waits and retries**. If the daily quota is used up
  or the key is wrong, it stops with a clear message.

---

## 11. Limitations

- The inspected repo's tests are not run (that is unsafe without a sandbox), so a broken test is
  only reported when it is obvious from reading the code.
- GitHub settings like branch protection can't be seen from files, so they are never reported.
- Very large repos are summarised folder by folder, and the history search covers the last 200 commits.
- On the free Gemini tier, Google may use the requests to improve its products.
