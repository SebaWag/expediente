"""Model access: one adapter for the real API, one for tests that never call it.

The Meta Model API is OpenAI-compatible, so the adapter is small. Two details are
not small, and both were found by probing the live endpoint rather than by reading
the docs:

1. **Reasoning tokens are billed against ``max_tokens``.** A request with
   ``max_tokens=120`` and ``reasoning_effort=low`` came back with 118 reasoning
   tokens, no content and **no tool call** — the model thought itself out of its own
   budget. Every call here therefore has a floor of :data:`MIN_OUTPUT_TOKENS`.
2. **The tool schema is part of the cached prefix.** ``cached_tokens`` was already
   non-zero on a cold call, which is only useful if the prefix does not churn: the
   tool list is ordered and never rebuilt per request.

:class:`ScriptedProvider` exists so the whole agent loop — including its failure
paths — can be tested offline and replayed deterministically. A test that needs a
model is not a test of this system, it is a test of Meta's uptime.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

#: A floor, not a preference. Below ~1000 the reasoning pass consumes the whole
#: allowance and the agent returns nothing while reporting success.
MIN_OUTPUT_TOKENS = 1000
DEFAULT_TIMEOUT_S = 120
RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """The model did not answer. Never returned as an empty completion."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON as the model emitted it

    def parsed(self) -> dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    reasoning_tokens: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Provider(Protocol):
    name: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "low",
        max_tokens: int = MIN_OUTPUT_TOKENS,
        tool_choice: str | None = None,
    ) -> ModelReply: ...


# ---------------------------------------------------------------------- real API
class MetaProvider:
    """``https://api.meta.ai/v1`` with ``muse-spark-1.3``.

    Retries on 429 and 5xx with backoff. It does **not** retry on 400: a malformed
    request is a bug on this side, and retrying it just spends money three times.
    """

    name = "meta"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or os.environ.get("META_MODEL_API_KEY", "")
        self.base_url = (base_url or os.environ.get("META_MODEL_BASE_URL") or "https://api.meta.ai/v1").rstrip("/")
        self.model = model or os.environ.get("META_MODEL_CHAT_MODEL") or "muse-spark-1.3"
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleeper
        if not self.api_key:
            raise ProviderError("META_MODEL_API_KEY is not set")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "low",
        max_tokens: int = MIN_OUTPUT_TOKENS,
        tool_choice: str | None = None,
    ) -> ModelReply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "reasoning_effort": reasoning_effort,
            "max_tokens": max(max_tokens, MIN_OUTPUT_TOKENS),
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
            # A parallel tool call would produce several graph queries whose results
            # the model then has to reconcile in one message; one at a time keeps the
            # log readable and the plan honest about what was decided when.
            body["parallel_tool_calls"] = False

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return _parse_reply(payload, self.model)
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:400].decode("utf-8", errors="replace")
                last_error = ProviderError(f"HTTP {exc.code}: {detail}")
                if exc.code not in RETRY_STATUS or attempt == self.max_retries:
                    raise last_error from exc
                self._sleep(2 ** attempt)
            except urllib.error.URLError as exc:
                last_error = ProviderError(f"unreachable: {exc.reason}")
                if attempt == self.max_retries:
                    raise last_error from exc
                self._sleep(2 ** attempt)
        raise last_error or ProviderError("no reply")


def _parse_reply(payload: dict[str, Any], model: str) -> ModelReply:
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError) as exc:
        raise ProviderError(f"malformed response: {json.dumps(payload)[:300]}") from exc
    message = choice.get("message") or {}
    raw_calls = message.get("tool_calls") or []
    calls: list[ToolCall] = []
    for index, call in enumerate(raw_calls):
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append(
            ToolCall(
                id=call.get("id") or f"call_{index}",
                name=function.get("name") or "",
                arguments=arguments or "{}",
            )
        )
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    return ModelReply(
        text=(message.get("content") or "").strip(),
        tool_calls=tuple(calls),
        usage=usage,
        model=payload.get("model") or model,
        reasoning_tokens=int(details.get("reasoning_tokens") or 0),
    )


# ------------------------------------------------------------------------ script
class ScriptedProvider:
    """A provider that replays a fixed script. Used by the tests and by the demo.

    ``calls`` records every request, so a test can assert *what the agent asked*,
    not only what it produced — which is the part that matters when the failure mode
    is "the model never used the second tool".
    """

    name = "scripted"

    def __init__(self, script: list[ModelReply], *, fail_on: int | None = None) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on
        self._index = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "low",
        max_tokens: int = MIN_OUTPUT_TOKENS,
        tool_choice: str | None = None,
    ) -> ModelReply:
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": [tool["function"]["name"] for tool in (tools or [])],
                "reasoning_effort": reasoning_effort,
                "max_tokens": max_tokens,
            }
        )
        if self.fail_on is not None and self._index == self.fail_on:
            raise ProviderError("scripted failure")
        if self._index >= len(self.script):
            return ModelReply(text="", tool_calls=(), model="scripted")
        reply = self.script[self._index]
        self._index += 1
        return reply


def tool_call(name: str, arguments: dict[str, Any] | str, call_id: str | None = None) -> ToolCall:
    """Small helper so a script reads like a plan instead of like JSON."""
    payload = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return ToolCall(id=call_id or f"call_{name}_{abs(hash(payload)) % 10000}", name=name, arguments=payload)
