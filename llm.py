"""The only file that talks to Google Gemini.

It keeps the conversation history, tells Gemini which tools exist, returns the tool calls
Gemini wants to make, and retries when the free tier is busy or rate-limited.
Deciding what to do with those tool calls happens in agent_loop.py, not here.
"""
import re
import time

import httpx
from google import genai
from google.genai import errors, types


class GeminiLLM:
    def __init__(self, api_key, model, system_prompt, tool_schemas, log=print):
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=180_000))
        self.model = model
        self.log = log
        self.history = []  # the whole conversation; Gemini is sent all of it every turn
        declarations = [
            types.FunctionDeclaration(name=s["name"], description=s["description"],
                                      parameters_json_schema=s["parameters"])
            for s in tool_schemas
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=declarations)],
            # We execute tools ourselves (agent_loop.py), so the SDK must never do it for us.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        # For the very last turn: Gemini may only call `finish`.
        self.final_config = self.config.model_copy(update={"tool_config": types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=["finish"]))})

    def send(self, text=None, tool_results=(), force_finish=False):
        """Send a message and/or tool results. Returns the tool calls Gemini wants next."""
        parts = [
            types.Part(function_response=types.FunctionResponse(
                id=call.get("id"), name=call["name"], response={"result": output}))
            for call, output in tool_results
        ]
        if text and parts:
            # Gemini 3.x wants extra instructions appended to the last tool result, not a separate part.
            last = parts[-1].function_response
            last.response = {"result": last.response["result"] + "\n\n" + text}
        elif text:
            parts.append(types.Part(text=text))
        self.history.append(types.Content(role="user", parts=parts))

        content = self._generate(self.final_config if force_finish else self.config)
        self.history.append(content)  # kept unchanged: it carries Gemini's thought signatures
        return [
            {"id": p.function_call.id, "name": p.function_call.name, "args": dict(p.function_call.args or {})}
            for p in content.parts if p.function_call
        ]

    def _generate(self, config, attempts=5):
        """One Gemini call, retried on rate limits (429), server errors and network problems."""
        wait = 5
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=self.history, config=config)
                candidate = response.candidates[0] if response.candidates else None
                if candidate and candidate.content and candidate.content.parts:
                    return candidate.content
                problem = f"empty reply ({candidate.finish_reason if candidate else 'no candidates'})"
            except errors.APIError as e:
                if e.code == 429 and "PerDay" in str(e):
                    raise RuntimeError("Gemini's free daily quota is used up (it resets at midnight Pacific "
                                       "time). Try later, or set another GEMINI_MODEL in .env.") from None
                if e.code in (400, 401, 403, 404):
                    raise RuntimeError(f"Gemini rejected the request ({e.code} {e.status}): {e.message}\n"
                                       "Check GEMINI_API_KEY and GEMINI_MODEL in your .env file.") from None
                problem = f"{e.code} {e.status}"
                wait = max(wait, _retry_delay(str(e)))
            except (httpx.TransportError, OSError) as e:
                problem = f"network problem ({type(e).__name__})"
            if attempt < attempts:
                self.log(f"     ! Gemini: {problem}. Retrying in {wait:.0f}s ({attempt}/{attempts - 1})")
                time.sleep(wait)
                wait = min(wait * 2, 60)
        raise RuntimeError(f"Gemini kept failing ({problem}). Check your internet connection and try again.")


def _retry_delay(message):
    """Gemini's 429 errors say how long to wait, e.g. 'retryDelay': '37s'."""
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", message)
    return float(match.group(1)) + 1 if match else 0
