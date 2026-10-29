"""Turning a web page into a citation, and refusing the ones that are not.

The graph's evidence invariant is: *a claim without a document and a page is
deleted*. A web source has no pages, so the equivalent here is stricter and
simpler — **a claim without a verbatim quote, found in the bytes we actually
downloaded, does not exist.**

Why the strict version and not "the model cited a URL"
------------------------------------------------------

A model that is asked to cite will produce a citation-shaped string whether or not
it read anything. That is not a hypothetical: it is the default failure of every
"add sources to the answer" feature. So the check cannot be "is there a URL". It
has to be mechanical:

    does this exact sentence appear in the text we fetched, at this offset?

That check needs no model, costs nothing, and cannot be talked around. It is the
same design decision as the deterministic detectors, applied to the one layer of
this system that talks to the open internet.

What it does not do
-------------------

It verifies **attribution**, not truth. A page can say something false and be
quoted perfectly. That is why every source carries ``url``, ``sha256`` and
``recuperado_en``: the claim is that *this page said this*, which a human can
re-check, and never that the page is right. Conflating the two is how an engine
like this becomes a rumour machine.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from .fetch import Source

#: Shorter than this and a "quote" is a word that appears in every document:
#: matching it proves nothing. 24 characters is roughly one clause in Spanish.
MIN_QUOTE_CHARS = 24
#: A quote may be elided with ``…`` or ``[...]``; each surviving fragment has to be
#: present, in order. Beyond this many fragments it is a collage, not a citation.
MAX_FRAGMENTS = 4

_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")


def _normalise(text: str) -> str:
    """Casefold, unify accents and collapse whitespace.

    Accents are folded because a page may write "Auditoría" and the model may quote
    "Auditoria". Case is folded because headline capitalisation is a rendering
    choice. Whitespace is collapsed because the extractor already re-flowed the
    paragraphs, so a line break is not part of what the page said.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WS.sub(" ", stripped).strip().casefold()


@dataclass(frozen=True)
class Quote:
    """A fragment that was found, with where it was found."""

    text: str
    offset: int
    context: str

    def summary(self) -> dict[str, Any]:
        return {"text": self.text, "offset": self.offset, "context": self.context}


def find_quote(text: str, quote: str, *, context_chars: int = 120) -> Quote | None:
    """Locate ``quote`` in ``text``, allowing elisions. ``None`` means it is not there.

    Returning ``None`` rather than raising is deliberate: a missing quote is the
    normal case for a model that paraphrased, and the caller has to record a
    rejection, not crash the run.
    """
    if not quote or not quote.strip():
        return None
    haystack = _normalise(text)
    fragments = [fragment for fragment in _ELLIPSIS.split(quote) if fragment.strip()]
    if not fragments or len(fragments) > MAX_FRAGMENTS:
        return None

    cursor = 0
    first_offset: int | None = None
    for fragment in fragments:
        needle = _normalise(fragment)
        if len(needle) < MIN_QUOTE_CHARS and len(fragments) == 1:
            return None
        position = haystack.find(needle, cursor)
        if position < 0:
            return None
        if first_offset is None:
            first_offset = position
        cursor = position + len(needle)

    assert first_offset is not None
    start = max(0, first_offset - context_chars // 2)
    end = min(len(haystack), cursor + context_chars // 2)
    return Quote(
        text=quote.strip(),
        offset=first_offset,
        context=("…" if start else "") + haystack[start:end].strip() + ("…" if end < len(haystack) else ""),
    )


REASON_NO_SOURCE = "la fuente no existe en el registro"
REASON_EMPTY_QUOTE = "no trae cita textual"
REASON_TOO_SHORT = f"la cita tiene menos de {MIN_QUOTE_CHARS} caracteres"
REASON_NOT_FOUND = "la cita no aparece en el documento descargado"
REASON_TOO_MANY = f"la cita está partida en más de {MAX_FRAGMENTS} fragmentos"


@dataclass(frozen=True)
class WebClaim:
    """An assertion backed by a verbatim quote from a retrieved document."""

    text: str
    source_id: str
    url: str
    sha256: str
    domain: str
    official: bool
    quotes: tuple[Quote, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source_id": self.source_id,
            "url": self.url,
            "domain": self.domain,
            "official": self.official,
            "quotes": [quote.summary() for quote in self.quotes],
        }


@dataclass
class SourceRegistry:
    """Every document retrieved during a run, deduplicated by content.

    Deduplication is by ``sha256``, not by URL: the same press release syndicated on
    four municipal sites is one source with four URLs, and counting it four times is
    how a weak claim acquires the appearance of corroboration.
    """

    _by_id: dict[str, Source] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    def add(self, source: Source) -> Source:
        existing = self._by_id.get(source.id)
        if existing is not None:
            # Same bytes, new URL: keep the first record and remember the alias, so a
            # second fetch of the same page does not double the evidence.
            self._aliases[source.url] = existing.id
            return existing
        self._by_id[source.id] = source
        self._aliases[source.url] = source.id
        self._aliases[source.requested_url] = source.id
        return source

    def get(self, source_id: str) -> Source | None:
        if source_id in self._by_id:
            return self._by_id[source_id]
        alias = self._aliases.get(source_id)
        return self._by_id.get(alias) if alias else None

    def all(self) -> list[Source]:
        return list(self._by_id.values())

    def summaries(self) -> list[dict[str, Any]]:
        return [source.summary() for source in self._by_id.values()]

    def official_count(self) -> int:
        return sum(1 for source in self._by_id.values() if source.official)


def verify_claim(
    registry: SourceRegistry,
    *,
    text: str,
    source_id: str,
    quotes: Iterable[str],
) -> tuple[WebClaim | None, str | None]:
    """Accept a web claim only if a quote is found verbatim. ``(claim, reason)``.

    One function, one place where this decision is made. The agent loop calls it,
    the portal calls it, and the tests call it — so a claim cannot be accepted on
    one path and rejected on another.
    """
    source = registry.get(source_id)
    if source is None:
        return None, REASON_NO_SOURCE

    quote_texts = [quote for quote in quotes if quote and quote.strip()]
    if not quote_texts:
        return None, REASON_EMPTY_QUOTE

    found: list[Quote] = []
    for quote_text in quote_texts:
        fragments = [fragment for fragment in _ELLIPSIS.split(quote_text) if fragment.strip()]
        if len(fragments) > MAX_FRAGMENTS:
            return None, REASON_TOO_MANY
        if len(fragments) == 1 and len(_normalise(quote_text)) < MIN_QUOTE_CHARS:
            return None, REASON_TOO_SHORT
        located = find_quote(source.text, quote_text)
        if located is None:
            return None, REASON_NOT_FOUND
        found.append(located)

    return (
        WebClaim(
            text=text.strip(),
            source_id=source.id,
            url=source.url,
            sha256=source.sha256,
            domain=source.domain,
            official=source.official,
            quotes=tuple(found),
        ),
        None,
    )
