"""Tests for the Gemini wrapper with the network call replaced by a stub (no API key needed)."""
import pytest
from google.genai import errors, types

import llm as llm_module
from agent_loop import ALL_TOOLS
from llm import GeminiLLM


def reply(*calls):
    parts = [types.Part(function_call=types.FunctionCall(id=f"c{i}", name=name, args=args))
             for i, (name, args) in enumerate(calls)]
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda _: None)  # no real waiting in tests
    model = GeminiLLM("fake-key", "gemini-test", "system prompt", ALL_TOOLS, log=lambda *_: None)
    model.queue, model.requests = [], []

    def fake_generate(**request):
        model.requests.append(request)
        item = model.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(model.client.models, "generate_content", fake_generate)
    return model


def test_tool_results_are_sent_back_with_matching_ids(gemini):
    gemini.queue = [reply(("list_files", {"reason": "look"})), reply(("finish", {"violations": []}))]
    calls = gemini.send(text="start")
    assert calls == [{"id": "c0", "name": "list_files", "args": {"reason": "look"}}]
    gemini.send(tool_results=[(calls[0], "3 committed files")], text="last turn", force_finish=True)
    response = gemini.history[2].parts[0].function_response
    assert response.id == "c0" and response.name == "list_files"
    assert response.response["result"] == "3 committed files\n\nlast turn"
    assert gemini.requests[1]["config"].tool_config.function_calling_config.allowed_function_names == ["finish"]


def test_rate_limits_are_retried(gemini):
    busy = errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "slow down",
                                           "details": [{"retryDelay": "2s"}]}})
    gemini.queue = [busy, reply(("list_files", {"reason": "look"}))]
    assert gemini.send(text="start")[0]["name"] == "list_files"
    assert len(gemini.requests) == 2


def test_daily_quota_and_bad_key_fail_with_clear_messages(gemini):
    daily = errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                            "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}})
    bad_key = errors.APIError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid"}})
    gemini.queue = [daily, bad_key]
    with pytest.raises(RuntimeError, match="daily quota"):
        gemini.send(text="start")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        gemini.send(text="start")
