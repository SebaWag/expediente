"""Fetching a web source so that it can later be cited.

The only interesting requirement here is that a fetched page must be **checkable
after the fact**. A citation that says "según la Contraloría" is an opinion with a
domain attached to it. A citation that says "this sentence, at this offset, of the
bytes with this sha256, retrieved at this time, from this URL" is evidence — a
second person can re-fetch the URL, hash what comes back, and see whether the
sentence is really there.

So this module records the raw digest, not just the text. Three consequences:

* **Text is extracted, bytes are hashed.** The extractor can change without
  invalidating what was recorded, because the hash is over the wire bytes.
* **Redirects are recorded, not followed silently.** A result that says it is on
  ``contraloria.cl`` and lands on a blog is a different source, and the record has
  to show both.
* **Private addresses are refused.** This fetcher is reachable from a public
  portal that takes user text, so ``http://127.0.0.1:1234/sparql`` is exactly the
  sort of URL that must not become a feature. The guard is in :func:`_assert_public`.

No third-party dependency: :mod:`urllib`, :mod:`html.parser`, :mod:`hashlib`.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable

#: A user agent that says who is fetching, because anonymous scrapers are the ones
#: that get blocked. This is a public-interest project reading public records.
USER_AGENT = (
    "Expediente/0.4 (+https://expediente.wagnersolutionsai.com; "
    "auditoría de compras públicas; contacto: sebastian@wagnersolutionsai.com)"
)

MAX_BYTES = 2_000_000
TIMEOUT_S = 25
#: Only the two ports a citation can plausibly live on. An allowlist, not a denylist:
#: the point is to make an internal port unreachable rather than to enumerate them.
ALLOWED_PORTS = {80, 443}


class FetchError(ValueError):
    """The source could not be retrieved. Never swallowed: an unfetched source is
    not an empty source, and treating the two the same is how a dossier ends up
    asserting something nothing supports."""


@dataclass(frozen=True)
class Source:
    """One retrieved document, in the shape a citation needs.

    The property names at the bottom are the ones the graph already uses for
    ``:Documento`` (``hash_sha256``, ``tipo``, ``url``), so a web source and a PDF
    source enter the same evidence chain and pass through the same gate. That is
    the whole reason this dataclass is not called ``WebPage``.
    """

    url: str
    requested_url: str
    fetched_at: str
    status: int
    content_type: str
    sha256: str
    title: str
    text: str
    n_bytes: int
    domain: str
    official: bool = False
    truncated: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        """Deterministic: the same bytes from the same URL are the same source."""
        return f"web:{self.sha256[:16]}"

    def documento_props(self) -> dict[str, Any]:
        """Exactly the ``:Documento`` properties the schema requires, plus provenance."""
        return {
            "hash_sha256": self.sha256,
            "tipo": "web",
            "url": self.url,
            "titulo": self.title or self.domain,
            "recuperado_en": self.fetched_at,
            "dominio": self.domain,
            "oficial": self.official,
        }

    def summary(self) -> dict[str, Any]:
        """What the UI shows in the sources panel, without the full text."""
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title or self.domain,
            "domain": self.domain,
            "official": self.official,
            "fetched_at": self.fetched_at,
            "sha256": self.sha256,
            "n_bytes": self.n_bytes,
            "chars": len(self.text),
            "truncated": self.truncated,
        }


# --------------------------------------------------------------------- extraction
class _TextExtractor(HTMLParser):
    """HTML -> readable text, with paragraph and heading structure kept.

    Kept deliberately small. Every tag that adds no words is skipped, and the ones
    that end a thought become a newline, because a citation that joins two
    sentences without a break is a citation that misquotes.
    """

    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
    BLOCK = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "blockquote", "table", "ul", "ol",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        self.chunks.append(data)

    def text(self) -> str:
        raw = "".join(self.chunks)
        raw = raw.replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)


def html_to_text(raw_html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        # A malformed page still has a prefix worth reading; the digest is over the
        # bytes anyway, so a partial extraction does not weaken the record.
        pass
    return re.sub(r"\s+", " ", html.unescape(parser.title)).strip(), parser.text()


# ------------------------------------------------------------------------- guard
#: The resolver is a parameter, not a constant, for one reason: a security test that
#: needs a working DNS is not a test, it is a coin flip. Tests inject a fake resolver
#: and exercise the guard against fixed addresses.
Resolver = Callable[[str, int], list[tuple]]


def _default_resolver(host: str, port: int) -> list[tuple]:
    return socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)


def _assert_public(
    url: str, resolver: Resolver | None = None
) -> urllib.parse.ParseResult:
    """Refuse anything that is not a public HTTP(S) resource.

    This is the SSRF guard, and it is not theoretical: the portal accepts a
    question from a browser and the agent turns that question into URLs. Every
    address a hostname resolves to is checked, because checking only the first is
    how a DNS round-robin walks past the guard.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise FetchError(f"scheme not allowed: {parsed.scheme!r} (only http and https)")
    if not parsed.hostname:
        raise FetchError(f"no host in {url!r}")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise FetchError(f"invalid port: {exc}") from exc
    if port not in ALLOWED_PORTS:
        raise FetchError(f"port {port} is not allowed (only 80 and 443)")

    host = parsed.hostname
    if host.lower() in {"localhost", "localhost.localdomain"}:
        raise FetchError("refusing to fetch localhost")

    # A literal address is checked directly, before any resolver is consulted. The
    # first version of this function only validated what the resolver returned, which
    # meant the guard depended on the resolver being honest — fine for a production
    # resolver, useless as a boundary, because the check has to hold for the bytes in
    # the URL and not for a side effect of a DNS call. Found by its own test.
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        _reject_non_public(literal)
        return parsed

    try:
        infos = (resolver or _default_resolver)(host, port)
    except (socket.gaierror, OSError) as exc:
        raise FetchError(f"cannot resolve {host!r}: {exc}") from exc
    if not infos:
        raise FetchError(f"{host!r} resolved to nothing")

    for info in infos:
        _reject_non_public(ipaddress.ip_address(info[4][0]))
    return parsed


def _reject_non_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Every address a name resolves to is checked: a round-robin must not walk past."""
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise FetchError(f"refusing to fetch a non-public address: {address}")


#: Domains that answer questions about Chilean public money. Being on this list does
#: not make a source true; it makes it *accountable*, which is the property a citation
#: needs. Everything else is still allowed and is labelled as not official.
OFFICIAL_DOMAINS: tuple[str, ...] = (
    "contraloria.cl",
    "mercadopublico.cl",
    "chilecompra.cl",
    "compra-agil.cl",
    "bcn.cl",
    "leychile.cl",
    "diariooficial.interior.gob.cl",
    "sii.cl",
    "registrocivil.cl",
    "interior.gob.cl",
    "hacienda.cl",
    "dipres.gob.cl",
    "ine.gob.cl",
    "consejotransparencia.cl",
    "probidad.cl",
    "serviciocivil.cl",
    "tdc.cl",
    "pjud.cl",
    "ministeriopublico.cl",
)


def classify_domain(url: str) -> tuple[str, bool]:
    """``("contraloria.cl", True)``.

    ``.gob.cl`` and ``.cl`` municipal hosts are official too: a municipality's own
    site is where a council member would look, and excluding it would make the tool
    useless for the audience it is for.
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for domain in OFFICIAL_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return host, True
    if host.endswith(".gob.cl") or host.endswith(".gov.cl"):
        return host, True
    return host, False


# ------------------------------------------------------------------------- fetch
Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def fetch(
    url: str,
    *,
    timeout: float = TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
    opener: Opener | None = None,
    resolver: Resolver | None = None,
    accept: str = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
) -> Source:
    """Retrieve one URL and return it as a citable source.

    ``opener`` exists so the tests can run offline against fixed bytes. Production
    always goes through :func:`_default_opener`.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    _assert_public(url, resolver)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "es-CL,es;q=0.9,en;q=0.5",
        },
    )
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        response = (opener or _default_opener)(request, timeout)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"cannot reach {url}: {exc.reason}") from exc
    except socket.timeout as exc:
        raise FetchError(f"timeout after {timeout}s: {url}") from exc

    with response:
        status = int(getattr(response, "status", None) or response.getcode() or 0)
        content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
        final_url = response.geturl() or url
        raw = response.read(max_bytes + 1)

    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]

    digest = hashlib.sha256(raw).hexdigest()
    domain, official = classify_domain(final_url)
    notes: list[str] = []
    if final_url != url:
        notes.append(f"redirected: {url} -> {final_url}")
    if truncated:
        notes.append(f"truncated at {max_bytes} bytes")

    if "html" in content_type or content_type == "":
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", str(response.headers.get("Content-Type", "")), re.I)
        if match:
            charset = match.group(1)
        try:
            decoded = raw.decode(charset, errors="replace")
        except LookupError:
            decoded = raw.decode("utf-8", errors="replace")
        title, text = html_to_text(decoded)
    elif content_type.startswith("text/") or "json" in content_type or "xml" in content_type:
        text = raw.decode("utf-8", errors="replace")
        title = text.strip().splitlines()[0][:200] if text.strip() else ""
    else:
        raise FetchError(f"unsupported content type {content_type!r} from {final_url} (no text layer)")

    if not text.strip():
        raise FetchError(f"no text extracted from {final_url}")

    return Source(
        url=final_url,
        requested_url=url,
        fetched_at=fetched_at,
        status=status,
        content_type=content_type or "unknown",
        sha256=digest,
        title=title,
        text=text,
        n_bytes=len(raw),
        domain=domain,
        official=official,
        truncated=truncated,
        notes=tuple(notes),
    )
