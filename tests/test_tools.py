"""Tests for the inspection tools. They run on a small local git repo: no network needed."""
import subprocess

from tools import git_log, list_files, read_file, redact, run_ruff, run_tool, search_code, search_history


def test_list_files_shows_only_committed_files(sample_repo):
    lines = list_files(sample_repo).splitlines()
    assert "app.py" in lines and "README.md" in lines
    assert "untracked.txt" not in lines and ".env" not in lines


def test_read_file_numbers_lines_and_rejects_bad_paths(sample_repo):
    assert "1: API_KEY" in read_file(sample_repo, "app.py")
    assert "not found" in read_file(sample_repo, "missing.py")
    assert "outside the repository" in run_tool("read_file", {"path": "../../etc/passwd"}, sample_repo)


def test_search_code_reports_file_and_line(sample_repo):
    assert "app.py:1:" in search_code(sample_repo, r"api_key\s*=")
    assert "plain text" in search_code(sample_repo, "print(")  # invalid regex still works


def test_git_tools_read_history(sample_repo):
    assert "remove env file" in git_log(sample_repo)
    history = search_history(sample_repo, "SECRET_TOKEN")
    assert ".env" in history and "removed" in history


def test_run_ruff_finds_undefined_name(sample_repo):
    assert "F821" in run_ruff(sample_repo)


def test_unknown_tool_and_bad_arguments_return_errors(sample_repo):
    assert "unknown tool" in run_tool("delete_everything", {}, sample_repo)
    assert run_tool("read_file", {"nonsense": 1}, sample_repo).startswith("Error")


def test_redact_masks_secrets_but_keeps_env_lookups():
    text = 'API_KEY = "a8f5f167f44f4964e6c998dee827110c"\nDB = "postgres://admin:hunter22@db/app"'
    masked = redact(text)
    assert "a8f5f167" not in masked and "hunter22" not in masked
    assert redact('API_KEY = os.getenv("API_KEY")') == 'API_KEY = os.getenv("API_KEY")'


def test_tools_never_modify_the_repo(sample_repo):
    def status():
        return subprocess.run(["git", "status", "--porcelain"], cwd=sample_repo,
                              capture_output=True, text=True, check=False).stdout

    before = status()
    for name, args in [("list_files", {}), ("read_file", {"path": "app.py"}), ("search_code", {"pattern": "key"}),
                       ("git_log", {}), ("search_history", {"pattern": "token"}), ("run_ruff", {})]:
        run_tool(name, args, sample_repo)
    assert status() == before
