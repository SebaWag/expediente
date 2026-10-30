"""The only two shapes that may leave the extraction layer.

A field, or an unverified name with a reason. There is deliberately no third
shape: no empty string standing in for an answer, no ``None`` a caller can forget
to check, and no confidence float that invites a threshold nobody can defend.

``Causa`` is an enum rather than prose so the reasons can be counted. The count is
the thing that goes in the run report, and the run report is what a reviewer reads
when a field is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Causa(str, Enum):
    """Why a field did not come back with a value.

    ``TRUNCATED`` and ``EMPTY_RESPONSE`` are separate on purpose. The first means
    the model ran out of output budget mid-thought, which is a parameter we chose.
    The second means it returned nothing at all, which is a bug or a refusal. Both
    read as "no value" downstream, so they must not read as "no value" here.
    """

    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    TRUNCATED = "TRUNCATED"
    INVALID_JSON = "INVALID_JSON"
    MISSING_FIELD = "MISSING_FIELD"
    NO_PAGE = "NO_PAGE"
    UNSUPPORTED_VALUE = "UNSUPPORTED_VALUE"
    HTTP_ERROR = "HTTP_ERROR"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True, slots=True)
class Page:
    """One page of text, already read out of the document.

    The extractor never opens a PDF. Reading a document and interpreting a
    document are different jobs with different failure modes, and the layer that
    knows about text layers and scanned images is ``evidence.pdf``.
    """

    numero: int
    texto: str


@dataclass(frozen=True, slots=True)
class ExtractedField:
    """A value plus the page a reviewer can turn to.

    ``extracto`` carries the literal substring the model quoted. It is not proof —
    it is a search string, so a human (or the verifier) can confirm the value is
    on the page rather than merely plausible.
    """

    campo: str
    valor: str
    pagina: int | None = None
    extracto: str = ""
    backend: str = ""

    def to_dict(self) -> dict:
        return {
            "campo": self.campo,
            "valor": self.valor,
            "pagina": self.pagina,
            "extracto": self.extracto,
            "backend": self.backend,
        }


@dataclass(frozen=True, slots=True)
class Unverified:
    """A field name we asked for and did not get. This is a result, not an error."""

    campo: str
    causa: Causa
    detalle: str = ""

    def to_dict(self) -> dict:
        return {"campo": self.campo, "causa": self.causa.value, "detalle": self.detalle}


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting, with reasoning tokens broken out.

    They are broken out because they are billed as completion tokens and they are
    the ones that eat the output budget. A cost report that hides them cannot
    explain a run that returned empty answers.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """One pass of one backend over one document."""

    documento_id: str
    backend: str
    campos: tuple[ExtractedField, ...] = ()
    unverified: tuple[Unverified, ...] = ()
    usage: Usage = field(default_factory=Usage)
    reasoning_effort: str | None = None
    finish_reason: str | None = None

    @property
    def ok(self) -> bool:
        """True only when every requested field came back with a page."""
        return not self.unverified

    @property
    def truncado(self) -> bool:
        return any(u.causa is Causa.TRUNCATED for u in self.unverified)

    def field(self, campo: str) -> ExtractedField | None:
        for extracted in self.campos:
            if extracted.campo == campo:
                return extracted
        return None

    def to_dict(self) -> dict:
        return {
            "documento_id": self.documento_id,
            "backend": self.backend,
            "campos": [c.to_dict() for c in self.campos],
            "unverified": [u.to_dict() for u in self.unverified],
            "usage": self.usage.to_dict(),
            "reasoning_effort": self.reasoning_effort,
            "finish_reason": self.finish_reason,
        }
