"""Web research with traceability.

Three modules, in the order they are used:

* :mod:`expediente.research.search` — find candidate URLs. Asserts nothing.
* :mod:`expediente.research.fetch` — retrieve one URL as bytes plus extracted text,
  recording the sha256 and the retrieval time.
* :mod:`expediente.research.sources` — accept a claim only when a verbatim quote is
  found in those bytes. The rest is rejected, with the reason recorded.

The product rule this implements: a web source is not a link, it is a document.
"""

from .fetch import (
    ALLOWED_PORTS,
    MAX_BYTES,
    OFFICIAL_DOMAINS,
    FetchError,
    Source,
    classify_domain,
    fetch,
    html_to_text,
)
from .search import ENDPOINT, MAX_RESULTS, SearchError, SearchHit, parse_results, search
from .sources import (
    MAX_FRAGMENTS,
    MIN_QUOTE_CHARS,
    Quote,
    SourceRegistry,
    WebClaim,
    find_quote,
    verify_claim,
)

__all__ = [
    "ALLOWED_PORTS",
    "ENDPOINT",
    "MAX_BYTES",
    "MAX_FRAGMENTS",
    "MAX_RESULTS",
    "MIN_QUOTE_CHARS",
    "OFFICIAL_DOMAINS",
    "FetchError",
    "Quote",
    "SearchError",
    "SearchHit",
    "Source",
    "SourceRegistry",
    "WebClaim",
    "classify_domain",
    "fetch",
    "find_quote",
    "html_to_text",
    "parse_results",
    "search",
    "verify_claim",
]
