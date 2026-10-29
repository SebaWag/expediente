"""Finding candidate URLs. Nothing here asserts anything.

Search is the least trustworthy layer of the whole system and is treated as such:
it returns *candidates*, which then have to be fetched and quoted before they can
support a sentence. The distinction matters, because "the search engine said so" is
the same epistemic status as "the model said so" — both are unverified pointers.

The backend is DuckDuckGo's HTML endpoint: no API key, no account, no tracking
parameter to leak, and it answers plain HTTP. When it fails, it fails loudly
(:class:`SearchError`) rather than returning ``[]``; an empty result and a broken
search must never look the same to the caller, because the agent would treat the
first as "there is nothing to find".
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from .fetch import USER_AGENT, classify_domain

ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 12


class SearchError(ValueError):
    """The search did not run. Different from 'the search found nothing'."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    domain: str
    official: bool

    def summary(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "domain": self.domain,
            "official": self.official,
        }


class _ResultParser(HTMLParser):
    """Reads DuckDuckGo's HTML result list.

    The markup is not a contract and will change. That is acceptable here because
    this parser is only a *pointer generator*: if it degrades, the run produces
    fewer candidates, not a wrong claim. A test pins the shape it was written
    against so a silent break shows up as a failing test.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            href = _unwrap(attributes.get("href") or "")
            if not href:
                return
            self._current = {"url": href, "title": "", "snippet": ""}
            self._capture = "title"
            self._buffer = []
        elif "result__snippet" in classes and self._current is not None:
            self._capture = "snippet"
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture is None:
            return
        value = "".join(self._buffer).strip()
        if self._capture == "title" and tag == "a":
            if self._current is not None:
                self._current["title"] = value
                self.results.append(self._current)
            self._capture = None
            self._buffer = []
        elif self._capture == "snippet" and tag in {"a", "div", "span"}:
            if self._current is not None and not self._current.get("snippet"):
                self._current["snippet"] = value
            self._capture = None
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps outbound links: ``//duckduckgo.com/l/?uddg=<encoded>``."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return ""


def parse_results(raw_html: str) -> list[SearchHit]:
    """Split from the network call so the parser is testable against fixed bytes."""
    parser = _ResultParser()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        pass

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for result in parser.results:
        url = result["url"]
        if not url:
            continue
        key = urllib.parse.urlparse(url)._replace(query="", fragment="").geturl()
        if key in seen:
            continue
        seen.add(key)
        domain, official = classify_domain(url)
        hits.append(
            SearchHit(
                title=result["title"] or domain,
                url=url,
                snippet=result.get("snippet", ""),
                domain=domain,
                official=official,
            )
        )
    return hits


def search(query: str, *, limit: int = 6, timeout: float = 20.0, opener=None) -> list[SearchHit]:
    """Run one search. Raises :class:`SearchError` when the search itself failed."""
    query = (query or "").strip()
    if len(query) < 3:
        raise SearchError("query too short")
    limit = max(1, min(int(limit or 6), MAX_RESULTS))

    request = urllib.request.Request(
        ENDPOINT,
        data=urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode(),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
            "Accept-Language": "es-CL,es;q=0.9",
        },
    )
    try:
        if opener is not None:
            response = opener(request, timeout)
        else:
            response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise SearchError(f"search endpoint answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SearchError(f"search endpoint unreachable: {exc.reason}") from exc
    except Exception as exc:
        raise SearchError(f"search failed: {type(exc).__name__}: {exc}") from exc

    with response:
        raw = response.read(3_000_000).decode("utf-8", errors="replace")

    hits = parse_results(raw)
    if not hits:
        # Distinguishable from "the site is down": the endpoint answered, with a
        # page this parser could not read. Worth surfacing, because it is the only
        # early warning that the markup changed.
        if "result__a" not in raw:
            raise SearchError("the search page had no results block (markup may have changed)")
    return hits[:limit]
