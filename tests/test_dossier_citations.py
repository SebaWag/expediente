"""A claim about nine orders must let a reviewer open nine lines.

The gate keeps a claim alive on the *first* whole citation it finds — that is the right rule
for a gate, which only has to answer "can this be checked at all". It is the wrong rule for
the artifact: a sentence that says "nine purchase orders" and shows one page reads as if that
page proves nine. The dossier therefore resolves every citation the claim actually has, and
these tests hold that line.

Two properties keep the change from being disruptive:

* a claim with one citation renders and hashes **exactly** as before, so no existing dossier
  changed its id; and
* a claim with several renders them all, grouped by document, and its id moves — because a
  dossier that cites nine lines is not the same document as one that cites one.
"""

from pathlib import Path

from expediente.dossier.build import build_dossier
from expediente.evidence.gate import evaluate
from expediente.evidence.models import Claim, Document, Evidence

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "dossier_synthetic.json"


def three_citations() -> tuple[list[Claim], list[Evidence], list[Document]]:
    document = Document(id="sha256:archivo", url="https://example.org/2021-11.zip", tipo="csv", paginas=None)
    evidence = [
        Evidence(id=f"EV-{line}", document_id=document.id, pagina=line, extracto=f"línea {line}")
        for line in (36_451, 765_954, 886_128)
    ]
    claims = [Claim(id="AF-1", texto="Nueve órdenes bajo el tope", evidence_ids=tuple(item.id for item in evidence))]
    return claims, evidence, [document]


def build(claims, evidence, documents, **overrides):
    report = evaluate(claims, evidence, documents)
    payload: dict = dict(
        titulo="Expediente de prueba",
        sujeto="Comprador X",
        generado_en="2026-10-31",
    )
    payload.update(overrides)
    return build_dossier(report, documents, evidence=evidence, **payload)


def test_every_traceable_citation_reaches_the_dossier():
    dossier = build(*three_citations())

    claim = dossier.claims[0]
    assert [citation.pagina for citation in claim.supporting] == [36_451, 765_954, 886_128]


def test_the_rendered_sources_list_every_page_not_just_the_first():
    rendered = build(*three_citations()).to_markdown()

    assert "Fuentes (3)" in rendered
    assert "p.36451" in rendered and "p.765954" in rendered and "p.886128" in rendered


def test_a_single_citation_dossier_renders_the_old_way_and_keeps_its_id():
    claims, evidence, documents = three_citations()
    one = ([Claim(id="AF-1", texto="Una orden", evidence_ids=("EV-36451",))], evidence[:1], documents)

    rendered = build(*one).to_markdown()

    assert "*Fuente:*" in rendered
    assert "Fuentes (" not in rendered
    # The identity payload must not grow a list for a claim that has one citation.
    assert "citations" not in build(*one).to_json()


def test_a_dossier_that_cites_nine_lines_is_not_the_dossier_that_cites_one():
    many = build(*three_citations())
    claims, evidence, documents = three_citations()
    one = ([Claim(id="AF-1", texto="Nueve órdenes bajo el tope", evidence_ids=("EV-36451",))], evidence, documents)

    assert many.id != build(*one).id


def test_evidence_without_a_page_does_not_become_a_citation():
    document = Document(id="sha256:archivo", url="https://example.org/x.zip", tipo="csv")
    # ``extracto`` is not decoration: the gate rejects a citation that quotes nothing
    # (EMPTY_EXTRACT). A page number alone is a location, not an extract.
    evidence = [
        Evidence(id="EV-ok", document_id=document.id, pagina=42, extracto="MONTO TOTAL: 6.988.900"),
        Evidence(id="EV-no-page", document_id=document.id, pagina=None, extracto="sin página"),
    ]
    claims = [Claim(id="AF-1", texto="Algo", evidence_ids=("EV-ok", "EV-no-page"))]

    dossier = build(claims, evidence, [document])

    assert [citation.pagina for citation in dossier.claims[0].supporting] == [42]


def test_without_evidence_the_gate_citation_is_still_shown():
    """The CLI path builds a dossier from a report alone; it must not lose the citation."""
    from expediente.ingest.records import load_dossier

    claims, evidence, documents = load_dossier(FIXTURE)
    report = evaluate(claims, evidence, documents)
    dossier = build_dossier(report, documents, titulo="t", sujeto="s", generado_en="2026-10-31")

    assert dossier.claims
    assert all(claim.supporting[0].pagina >= 1 for claim in dossier.claims)
