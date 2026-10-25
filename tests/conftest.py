"""Shared test helpers: a tiny git repo with known problems, and a fake LLM."""
import subprocess

import pytest


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def sample_repo(tmp_path):
    """A repo with a hardcoded key, an undefined name, and a .env file deleted in a later commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("# Demo\n")
    (repo / "app.py").write_text('API_KEY = "a8f5f167f44f4964e6c998dee827110c"\nprint(undefined_name)\n')
    (repo / ".env").write_text("SECRET_TOKEN=abc123def456ghi789\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first commit")
    git(repo, "rm", "-q", ".env")
    git(repo, "commit", "-q", "-m", "remove env file")
    (repo / "untracked.txt").write_text("not committed\n")
    return repo


class FakeLLM:
    """Stands in for Gemini: replies with scripted tool calls and records everything it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, text=None, tool_results=(), force_finish=False):
        self.sent.append({"text": text, "tool_results": list(tool_results), "force_finish": force_finish})
        return self.replies.pop(0) if self.replies else []


def call(name, **args):
    return {"id": f"id-{name}", "name": name, "args": args}
