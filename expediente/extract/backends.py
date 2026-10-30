"""Backends: one OpenAI-compatible call, and everything that can go wrong with it.

There are two of them, and the difference between them is a deployment, not an
interface. ``ZeroShotBackend`` runs on serverless against a published model
(``muse-spark-open``); ``TunedBackend`` runs against an on-demand deployment of a
LoRA adapter. Both return the same ``ExtractionResult``, which is what makes the
tuned model a nice-to-have instead of a dependency: dropping it changes a line of
configuration, not a code path.

The parsing is a pure function, ``parse_completion``, separate from the HTTP call.
That is deliberate — every failure mode worth testing (empty content, truncated
output, prose instead of JSON, a value with no page) is a dict away, so the tests
never need the network and never need a credit.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, Sequence

from .. import config
from .models import Causa, ExtractedField, ExtractionResult, Page, Unverified, Usage
from .schema import PROCUREMENT_FIELDS, FieldSpec, system_prompt, user_prompt

#: Output budget. Exposed because it is the parameter that produced the empty
#: answers in the public run this layer was designed against: reasoning tokens are
#: billed as completion tokens, so a small cap plus high effort equals silence.
DEFAULT_MAX_TOKENS = 2048

#: The cheap setting, chosen from a measured sweep rather than from taste: on
#: short-answer extraction, `low` beat `high` by ~7 points on the same test set.
DEFAULT_EFFORT = "low"

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class NotConfigured(RuntimeError):
    """The backend exists but is not deployed. Degrading is the caller's decision."""


class Backend(Protocol):
    """What a backend is allowed to be."""

    name: str

    def extract(self, documento_id: str, pages: Sequence[Page], specs: Sequence[FieldSpec] | None = None) -> ExtractionResult:
        ...


@dataclass(frozen=True, slots=True)
class Completion:
    """The raw pieces of one response that matter, normalised."""

    content: str
    finish_reason: str | None
    usage: Usage


def _strip_fence(text: str) -> str:
    return _FENCE.sub("", text or "").strip()


def parse_completion(
    completion: Completion,
    documento_id: str,
    backend: str,
    specs: Sequence[FieldSpec] | None = None,
    reasoning_effort: str | None = None,
) -> ExtractionResult:
    """Turn a completion into fields, or into named absences. Never into silence."""
    selected = tuple(specs) if specs is not None else PROCUREMENT_FIELDS
    requested = [spec.nombre for spec in selected]

    def all_unverified(causa: Causa, detalle: str) -> ExtractionResult:
        return ExtractionResult(
            documento_id=documento_id,
            backend=backend,
            campos=(),
            unverified=tuple(Unverified(nombre, causa, detalle) for nombre in requested),
            usage=completion.usage,
            reasoning_effort=reasoning_effort,
            finish_reason=completion.finish_reason,
        )

    body = _strip_fence(completion.content)
    if not body:
        # The measured failure: reasoning consumed the whole output budget.
        if completion.finish_reason == "length":
            return all_unverified(
                Causa.TRUNCATED,
                f"finish_reason=length, reasoning_tokens={completion.usage.reasoning_tokens}",
            )
        return all_unverified(Causa.EMPTY_RESPONSE, "empty content")

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        return all_unverified(Causa.INVALID_JSON, f"{error.msg} at {error.pos}")

    if not isinstance(parsed, dict) or not isinstance(parsed.get("campos"), dict):
        return all_unverified(Causa.INVALID_JSON, "missing 'campos' object")

    provided: dict[str, object] = parsed["campos"]
    campos: list[ExtractedField] = []
    unverified: list[Unverified] = []

    for spec in selected:
        entry = provided.get(spec.nombre)
        if entry is None:
            causa = Causa.TRUNCATED if completion.finish_reason == "length" else Causa.MISSING_FIELD
            unverified.append(Unverified(spec.nombre, causa, "not returned"))
            continue
        if not isinstance(entry, dict):
            unverified.append(Unverified(spec.nombre, Causa.UNSUPPORTED_VALUE, "entry is not an object"))
            continue

        raw_valor = entry.get("valor")
        valor = "" if raw_valor is None else str(raw_valor).strip()
        if not valor:
            unverified.append(Unverified(spec.nombre, Causa.UNSUPPORTED_VALUE, "empty value"))
            continue

        pagina = entry.get("pagina")
        if isinstance(pagina, str) and pagina.strip().isdigit():
            pagina = int(pagina)
        if not isinstance(pagina, int):
            # A value nobody can turn to is not evidence. The gate downstream would
            # drop the claim anyway; dropping it here keeps the count honest.
            unverified.append(Unverified(spec.nombre, Causa.NO_PAGE, "value returned without a page"))
            continue

        campos.append(
            ExtractedField(
                campo=spec.nombre,
                valor=valor,
                pagina=pagina,
                extracto=str(entry.get("extracto", "") or "")[:400],
                backend=backend,
            )
        )

    return ExtractionResult(
        documento_id=documento_id,
        backend=backend,
        campos=tuple(campos),
        unverified=tuple(unverified),
        usage=completion.usage,
        reasoning_effort=reasoning_effort,
        finish_reason=completion.finish_reason,
    )


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https endpoint
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        return error.code, {"error": detail}
    except urllib.error.URLError as error:
        return 0, {"error": str(error.reason)}


@dataclass
class ZeroShotBackend:
    """A published model on serverless. Always available; the default for a demo."""

    model: str = "accounts/fireworks/models/muse-spark-open"
    effort: str = DEFAULT_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: float = 300.0
    base_url: str | None = None
    api_key: str | None = None

    @property
    def name(self) -> str:
        return f"{self.model.rsplit('/', 1)[-1]}@{self.effort}"

    def _credentials(self) -> tuple[str, str]:
        return (
            self.api_key or config.fireworks_api_key(),
            self.base_url or config.fireworks_base_url(),
        )

    def payload(self, pages: Sequence[Page], specs: Sequence[FieldSpec] | None) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt(specs)},
                {"role": "user", "content": user_prompt(pages, specs)},
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.effort,
        }

    def extract(self, documento_id: str, pages: Sequence[Page], specs: Sequence[FieldSpec] | None = None) -> ExtractionResult:
        api_key, base_url = self._credentials()
        status, body = _post_json(f"{base_url.rstrip('/')}/chat/completions", self.payload(pages, specs), api_key, self.timeout)
        if status != 200:
            return ExtractionResult(
                documento_id=documento_id,
                backend=self.name,
                unverified=tuple(
                    Unverified(spec.nombre, Causa.HTTP_ERROR, f"status={status}") for spec in (specs or PROCUREMENT_FIELDS)
                ),
                reasoning_effort=self.effort,
            )
        return parse_completion(self._completion(body), documento_id, self.name, specs, self.effort)

    @staticmethod
    def _completion(body: dict) -> Completion:
        choice = (body.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        usage = body.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return Completion(
            content=message.get("content") or "",
            finish_reason=choice.get("finish_reason"),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                reasoning_tokens=int(details.get("reasoning_tokens") or 0),
            ),
        )


@dataclass
class TunedBackend(ZeroShotBackend):
    """A LoRA adapter on an on-demand deployment.

    Not serverless-callable, which is why this is the nice-to-have half of the
    pair: it needs a deployed replica (billed per GPU-hour, with a cold start) that
    the serverless path does not. Configure with ``EXPEDIENTE_EXTRACT_DEPLOYMENT``.
    """

    model: str = ""
    deployment_id: str | None = None
    effort: str = "low"

    @property
    def name(self) -> str:
        # Reads config as well as the field: the backend is usually constructed
        # with no arguments by the registry, and a report that says "lora:unset"
        # about a deployed adapter is worse than no label at all.
        deployment = self.deployment_id or config.extract_deployment() or "unset"
        return f"lora:{deployment}@{self.effort}"

    def resolve_model(self) -> str:
        deployment = self.deployment_id or config.extract_deployment()
        if not deployment:
            raise NotConfigured(
                "No tuned extractor deployed. Set EXPEDIENTE_EXTRACT_DEPLOYMENT to the "
                "Fireworks deployment id to use the LoRA, or leave it unset and the "
                "serverless backend answers."
            )
        account = config.fireworks_account_id()
        if not account:
            raise NotConfigured("FIREWORKS_ACCOUNT_ID is not set, so the deployment path cannot be built.")
        return f"accounts/{account}/deployments/{deployment}"

    def extract(self, documento_id: str, pages: Sequence[Page], specs: Sequence[FieldSpec] | None = None) -> ExtractionResult:
        model = self.resolve_model()
        api_key, base_url = self._credentials()
        payload = self.payload(pages, specs)
        payload["model"] = model
        status, body = _post_json(f"{base_url.rstrip('/')}/chat/completions", payload, api_key, self.timeout)
        if status != 200:
            return ExtractionResult(
                documento_id=documento_id,
                backend=self.name,
                unverified=tuple(
                    Unverified(spec.nombre, Causa.HTTP_ERROR, f"status={status}") for spec in (specs or PROCUREMENT_FIELDS)
                ),
                reasoning_effort=self.effort,
            )
        return parse_completion(self._completion(body), documento_id, self.name, specs, self.effort)


@dataclass
class MockBackend:
    """A backend that replays canned completions.

    It exists so the harness can be run end to end for zero credits, and so the
    tests can assert on the ugly cases — truncation, prose, a value with no page —
    without mocking urllib in four places.
    """

    completions: Sequence[Completion]
    name: str = "mock"
    effort: str | None = None

    def extract(self, documento_id: str, pages: Sequence[Page], specs: Sequence[FieldSpec] | None = None) -> ExtractionResult:
        index = getattr(self, "_cursor", 0)
        if index >= len(self.completions):
            raise RuntimeError("MockBackend ran out of canned completions")
        self._cursor = index + 1
        return parse_completion(self.completions[index], documento_id, self.name, specs, self.effort)
