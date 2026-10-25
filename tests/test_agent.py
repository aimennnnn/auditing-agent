"""Tests for the agent loop and the CLI, using a fake LLM (no API key or network needed)."""
import json

from conftest import FakeLLM, call

import agent
from agent_loop import FIELDS, audit, clean_violations

VIOLATION = {"rule_id": "SEC-001", "category": "security", "severity": "critical", "file": "app.py", "line": 1,
             "description": "Hardcoded API key",
             "evidence": 'app.py line 1: API_KEY = "a8f5f167f44f4964e6c998dee827110c"',
             "recommendation": "Read the key from an environment variable"}


def quiet(*_):
    pass


def test_agent_runs_the_tools_the_model_chooses(sample_repo):
    llm = FakeLLM([
        [call("list_files", reason="see the structure")],
        [call("read_file", path="app.py", reason="check config"), call("search_code", pattern="key", reason="keys")],
        [call("finish", violations=[VIOLATION])],
    ])
    violations, trace = audit(llm, sample_repo, "demo", log=quiet)
    assert [t["tool"] for t in trace] == ["list_files", "read_file", "search_code"]
    assert "app.py" in llm.sent[1]["tool_results"][0][1]  # the list_files result went back to the model
    assert len(llm.sent[2]["tool_results"]) == 2           # both parallel calls were answered
    assert violations[0]["rule_id"] == "SEC-001"
    assert "a8f5f167" not in json.dumps(violations)        # the secret is masked in the report


def test_report_is_cleaned_and_deduplicated():
    odd = {**VIOLATION, "rule_id": "hyg-001", "severity": "URGENT", "file": "", "line": 0}
    cleaned = clean_violations([VIOLATION, VIOLATION, odd, "not a dict"])
    assert len(cleaned) == 2
    assert all(list(v) == FIELDS for v in cleaned)
    hyg = next(v for v in cleaned if v["rule_id"] == "HYG-001")
    assert hyg["severity"] == "medium" and hyg["file"] is None and hyg["line"] is None


def test_agent_forces_finish_when_out_of_steps(sample_repo):
    llm = FakeLLM([[call("list_files", reason="look")]] * 3 + [[call("finish", violations=[])]])
    violations, _ = audit(llm, sample_repo, "demo", max_steps=3, log=quiet)
    assert violations == []
    assert llm.sent[-1]["force_finish"] is True


def test_agent_nudges_a_model_that_stops_calling_tools(sample_repo):
    llm = FakeLLM([[], [call("finish", violations=[])]])
    audit(llm, sample_repo, "demo", log=quiet)
    assert "Continue" in llm.sent[1]["text"]


def test_tool_errors_go_back_to_the_model(sample_repo):
    llm = FakeLLM([[call("read_file", path="nope.py", reason="x")], [call("finish", violations=[])]])
    audit(llm, sample_repo, "demo", log=quiet)
    assert "not found" in llm.sent[1]["tool_results"][0][1]


def test_cli_rejects_a_bad_repo_url(tmp_path, capsys):
    assert agent.main(["--repo", "not-a-repo", "--output", str(tmp_path / "r.json")]) == 1
    assert "not a GitHub repository URL" in capsys.readouterr().err


def test_cli_writes_a_valid_report(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(agent, "GeminiLLM", lambda *a, **k: FakeLLM([[call("finish", violations=[VIOLATION])]]))
    out = tmp_path / "report.json"
    assert agent.main(["--repo", str(sample_repo), "--output", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert list(report) == ["repository", "violations"] and len(report["violations"]) == 1
    assert out.with_suffix(".trace.json").exists()
