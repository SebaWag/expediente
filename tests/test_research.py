"""Web research: offline tests for the only layer that touches the open internet.

Nothing here reaches the network. The fetcher takes an ``opener`` and the search
parser takes bytes, so every case — including the hostile ones — is reproducible.
"""

import pytest

from expediente.research.fetch import FetchError, Source, classify_domain, fetch, html_to_text
from expediente.research.search import SearchError, parse_results, search as run_search
from expediente.research.sources import (
    SourceRegistry,
    find_quote,
    verify_claim,
)

# --------------------------------------------------------------------- fixtures
CONTRALORIA_HTML = """
<html><head><title>Informe Final N° 412-2025</title>
<style>body{color:red}</style><script>var x = 1;"no es texto"</script></head>
<body>
  <h1>Informe Final</h1>
  <p>La municipalidad adquirió insumos médicos mediante  seis órdenes de compra
     por un total de 54.582.378 pesos, sin acreditar la licitación previa.</p>
  <p>Se recomienda instruir un procedimiento disciplinario.</p>
</body></html>
"""

DDG_HTML = """
<div class="result results_links">
  <h2><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.contraloria.cl%2Finforme%2F412-2025&amp;rut=abc">
     Informe Final 412-2025 - Contraloría</a></h2>
  <a class="result__snippet">La municipalidad adquirió insumos médicos ...</a>
</div>
<div class="result results_links">
  <h2><a rel="nofollow" class="result__a" href="https://example.com/nota">Una nota de prensa</a></h2>
  <a class="result__snippet">Cobertura periodística del caso.</a>
</div>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fnota">
  Una nota de prensa</a>
"""


class FakeResponse:
    def __init__(self, body: bytes, *, status=200, content_type="text/html; charset=utf-8", url="https://www.contraloria.cl/informe/412-2025"):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._url = url

    def read(self, _n=None):
        return self._body

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


#: A resolver that answers with a public address, so nothing here touches DNS.
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]
PRIVATE = [(2, 1, 6, "", ("10.0.0.5", 443))]


def resolver_public(_host, _port):
    return PUBLIC


def resolver_private(_host, _port):
    return PRIVATE


def opener_returning(body: bytes, **kwargs):
    def opener(_request, _timeout):
        return FakeResponse(body, **kwargs)

    return opener


# ------------------------------------------------------------------ extraction
def test_html_becomes_text_and_sentences_are_not_glued_together():
    title, text = html_to_text(CONTRALORIA_HTML)
    assert title.startswith("Informe Final N° 412-2025")
    assert "insumos médicos" in text
    assert "no es texto" not in text, "script bodies are not text"
    assert "color:red" not in text, "style bodies are not text"
    assert "Se recomienda instruir" in text


# ----------------------------------------------------------------------- fetch
def test_fetch_records_the_digest_of_the_bytes_not_of_the_text():
    source = fetch(
        "https://www.contraloria.cl/informe/412-2025",
        opener=opener_returning(CONTRALORIA_HTML.encode()),
        resolver=resolver_public,
    )
    assert source.status == 200
    assert len(source.sha256) == 64
    assert source.official is True
    assert source.domain == "contraloria.cl", "the canonical host, without www"
    assert source.documento_props()["tipo"] == "web"
    assert source.documento_props()["hash_sha256"] == source.sha256


def test_the_same_bytes_produce_the_same_source_id():
    first = fetch("https://www.contraloria.cl/a", resolver=resolver_public, opener=opener_returning(b"<p>hola mundo</p>"))
    second = fetch("https://www.contraloria.cl/b", resolver=resolver_public, opener=opener_returning(b"<p>hola mundo</p>"))
    assert first.id == second.id, "identity is the content, not the URL"


def test_a_redirect_is_recorded_because_the_domain_changes_the_claim():
    source = fetch(
        "https://www.contraloria.cl/informe",
        opener=opener_returning(b"<p>texto</p>", url="https://blog.example.com/copia"),
        resolver=resolver_public,
    )
    assert source.url == "https://blog.example.com/copia"
    assert source.requested_url == "https://www.contraloria.cl/informe"
    assert any("redirected" in note for note in source.notes)
    assert source.official is False


def test_a_page_without_text_is_an_error_not_an_empty_source():
    with pytest.raises(FetchError):
        fetch("https://www.contraloria.cl/x", resolver=resolver_public, opener=opener_returning(b"   <div></div>  "))


def test_a_pdf_without_a_text_layer_is_refused_here():
    with pytest.raises(FetchError, match="content type"):
        fetch(
            "https://www.contraloria.cl/x.pdf",
            opener=opener_returning(b"%PDF-1.7", content_type="application/pdf"),
            resolver=resolver_public,
        )


def test_truncation_is_declared_not_silent():
    source = fetch(
        "https://www.contraloria.cl/largo",
        max_bytes=10,
        opener=opener_returning(b"<p>" + b"a" * 5000 + b"</p>"),
        resolver=resolver_public,
    )
    assert source.truncated is True
    assert any("truncated" in note for note in source.notes)


# ------------------------------------------------------------- the SSRF guard
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:80/admin",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://[::1]/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://example.com:1234/sparql",
    ],
)
def test_the_fetcher_refuses_anything_that_is_not_a_public_http_resource(url):
    """The portal takes text from a browser, so it takes URLs from a browser."""
    with pytest.raises(FetchError):
        fetch(url, resolver=resolver_private, opener=opener_returning(b"<p>x</p>"))


def test_official_domains_are_recognised_and_so_is_a_gob_cl_municipality():
    assert classify_domain("https://www.contraloria.cl/x") == ("contraloria.cl", True)
    assert classify_domain("https://compras.mercadopublico.cl/x") == ("compras.mercadopublico.cl", True)
    assert classify_domain("https://www.renca.cl/x") == ("renca.cl", False)
    assert classify_domain("https://munirena.gob.cl/x")[1] is True
    assert classify_domain("https://elmostrador.cl/x")[1] is False


# ---------------------------------------------------------------------- search
def test_search_parser_unwraps_redirects_and_deduplicates():
    hits = parse_results(DDG_HTML)
    urls = [hit.url for hit in hits]
    assert "https://www.contraloria.cl/informe/412-2025" in urls
    assert len(urls) == len(set(urls)), "the same URL twice is one candidate"
    assert hits[0].official is True
    assert hits[1].official is False


def test_search_raises_when_the_markup_has_no_results_block():
    with pytest.raises(SearchError):
        run_search("cardiotec vascular", opener=opener_returning(b"<html>nada</html>"))


def test_search_raises_on_http_error_instead_of_returning_nothing():
    def boom(_request, _timeout):
        raise RuntimeError("network down")

    with pytest.raises(SearchError):
        run_search("cardiotec vascular", opener=boom)


# ----------------------------------------------------------- the citation rule
def test_a_verbatim_quote_is_found_even_across_a_line_break_and_case():
    _, text = html_to_text(CONTRALORIA_HTML)
    quote = find_quote(text, "adquirió insumos médicos mediante seis órdenes de compra")
    assert quote is not None
    assert quote.offset >= 0


def test_a_paraphrase_is_not_found_and_therefore_cannot_be_cited():
    _, text = html_to_text(CONTRALORIA_HTML)
    assert find_quote(text, "compró insumos médicos sin licitación") is None


def test_an_elided_quote_is_accepted_only_when_every_fragment_is_present():
    _, text = html_to_text(CONTRALORIA_HTML)
    assert find_quote(text, "La municipalidad adquirió insumos médicos … sin acreditar la licitación previa") is not None
    assert find_quote(text, "La municipalidad adquirió insumos médicos … una frase que no existe") is None


def test_a_two_word_quote_proves_nothing_and_is_refused():
    _, text = html_to_text(CONTRALORIA_HTML)
    assert find_quote(text, "municipalidad") is None


def test_verify_claim_accepts_only_a_quote_that_exists():
    source = fetch("https://www.contraloria.cl/informe/412-2025", resolver=resolver_public, opener=opener_returning(CONTRALORIA_HTML.encode()))
    registry = SourceRegistry()
    registry.add(source)

    claim, reason = verify_claim(
        registry,
        text="Las seis órdenes suman 54.582.378 pesos",
        source_id=source.id,
        quotes=["adquirió insumos médicos mediante seis órdenes de compra por un total de 54.582.378 pesos"],
    )
    assert reason is None
    assert claim is not None
    assert claim.sha256 == source.sha256
    assert claim.url == source.url

    claim, reason = verify_claim(
        registry,
        text="La Contraloría concluyó que hubo fraude",
        source_id=source.id,
        quotes=["la Contraloría concluyó que hubo fraude"],
    )
    assert claim is None
    assert "no aparece" in reason


def test_a_claim_without_a_quote_is_refused_with_a_reason():
    source = fetch("https://www.contraloria.cl/x", resolver=resolver_public, opener=opener_returning(CONTRALORIA_HTML.encode()))
    registry = SourceRegistry()
    registry.add(source)
    claim, reason = verify_claim(registry, text="algo", source_id=source.id, quotes=["", "   "])
    assert claim is None
    assert reason


def test_a_claim_cannot_cite_a_source_that_was_never_fetched():
    registry = SourceRegistry()
    claim, reason = verify_claim(
        registry, text="algo", source_id="web:deadbeefdeadbeef", quotes=["una cita suficientemente larga como para pasar"]
    )
    assert claim is None
    assert "registro" in reason


def test_the_registry_counts_syndicated_copies_once():
    body = b"<p>La misma nota en cuatro sitios municipales distintos</p>"
    registry = SourceRegistry()
    for host in ("https://renca.cl/n", "https://maipu.cl/n", "https://puentealto.cl/n"):
        registry.add(fetch(host, resolver=resolver_public, opener=opener_returning(body, url=host)))
    assert len(registry.all()) == 1, "same bytes are one source, however many URLs"


def test_a_source_summary_carries_everything_a_reader_needs_to_recheck_it():
    source = fetch("https://www.contraloria.cl/informe/412-2025", resolver=resolver_public, opener=opener_returning(CONTRALORIA_HTML.encode()))
    summary = source.summary()
    for key in ("id", "url", "title", "domain", "official", "fetched_at", "sha256"):
        assert summary.get(key) not in (None, "")
