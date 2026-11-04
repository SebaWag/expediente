"""Materializa el vínculo que el detector ya conoce: de qué organismo y proveedor habla.

Por qué existe
--------------

Los detectores escribieron ``organismo_nombre`` y ``proveedor_nombre`` como **texto**, no como
arista. Por eso el grafo tenía cinco islas: cuatro detectores colgaban sus señales de la nada,
mientras el quinto (fraccionamiento) quedaba unido al resto a través de las órdenes.

La relación existía en la práctica —``signals_for()`` la resolvía en Python, por nombre, cada vez
que alguien preguntaba— pero no en el grafo: no se podía navegar, ni dibujar, ni citar.

El hallazgo que hizo esto posible
---------------------------------

En **435 señales** el campo no trae un nombre: trae un **RUT**. Eso no era un defecto a corregir
antes de vincular, era la clave: ``Organismo`` y ``Proveedor`` se identifican por ``rut_norm``.
Resolviendo el RUT se vinculan 435 señales de forma exacta, sin fuzzy de ningún tipo.

    435 por RUT · 85 por nombre · 42 parciales · 3 sin coincidencia   (565 en total)

La regla
--------

1. **RUT**: si el texto es un RUT y existe ese nodo, ese es el vínculo. Es la clave, no una
   semejanza.
2. **Nombre exacto** (normalizado: mayúsculas, sin acentos, sin puntos ni comas).
3. **Núcleo**: el nombre sin la forma jurídica ("LTDA", "S.A.", "SPA").
4. **Ambigüedad = no vincular.** Si dos entidades empatan, se descarta y se cuenta. Un vínculo
   equivocado entre una señal y una empresa es una afirmación falsa.

Uso
---

    python3 scripts/link_signal_parties.py            # dry run
    python3 scripts/link_signal_parties.py --apply    # escribe las aristas
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter

sys.path.insert(0, "/app")

from expediente.graph.millenniumdb import MillenniumDBStore, node_uri  # noqa: E402
from expediente.graph.schema import ONTO, RDF_TYPE, relation_uri      # noqa: E402

SUFIJOS = ("LTDA", "LIMITADA", "S A", "SA", "SPA", "S P A", "EIRL", "E I R L",
           "SOCIEDAD ANONIMA", "DE CHILE", "CHILE")

RUT_RE = re.compile(r"^([0-9]{1,2})[.\s]?([0-9]{3})[.\s]?([0-9]{3})[-\s]?([0-9Kk])$")


def norm(text: str | None) -> str:
    """Forma canónica de un nombre: sin acentos, sin puntuación, sin espacios de más."""
    raw = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    for ch in ".,;:()[]/\\-_":
        raw = raw.replace(ch, " ")
    return " ".join(raw.upper().split())


def como_rut(text: str | None) -> str | None:
    """Devuelve el RUT en la forma que usa el grafo (``12.345.678-9``) o None."""
    match = RUT_RE.match(str(text or "").strip())
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}-{match.group(4).upper()}"


def nucleo(text: str | None) -> str:
    """El nombre sin la forma jurídica, para comparar empresas que se nombran distinto."""
    base = norm(text)
    cambio = True
    while cambio:
        cambio = False
        for sufijo in SUFIJOS:
            if base.endswith(" " + sufijo):
                base = base[: -(len(sufijo) + 1)].strip()
                cambio = True
    return base


def cargar(client, label: str) -> tuple[set[str], dict[str, list[str]]]:
    """Devuelve (conjunto de claves RUT, índice por nombre) de un tipo de entidad."""
    rows = client.select(
        f"SELECT ?k ?n WHERE {{ ?x <{RDF_TYPE}> <{ONTO}{label}> . ?x <{ONTO}rut_norm> ?k . "
        f"OPTIONAL {{ ?x <{ONTO}{'nombre' if label == 'Organismo' else 'razon_social'}> ?n }} }}"
    )
    rutas: set[str] = set()
    por_nombre: dict[str, list[str]] = {}
    for row in rows:
        clave = str(row["k"])
        rutas.add(clave)
        nombre = row.get("n")
        if not nombre:
            continue
        for clave_nombre in (norm(nombre), "~" + nucleo(nombre)):
            if not clave_nombre.strip("~"):
                continue
            por_nombre.setdefault(clave_nombre, [])
            if clave not in por_nombre[clave_nombre]:
                por_nombre[clave_nombre].append(clave)
    return rutas, por_nombre


def resolver(texto: str | None, rutas: set[str], por_nombre: dict[str, list[str]]) -> tuple[str | None, str]:
    """(clave, modo). El modo se declara: así el resultado se puede auditar."""
    if not str(texto or "").strip():
        return None, "vacio"
    rut = como_rut(texto)
    if rut and rut in rutas:
        return rut, "rut"
    clave = norm(texto)
    candidatos = por_nombre.get(clave)
    if candidatos and len(candidatos) == 1:
        return candidatos[0], "nombre"
    if candidatos:
        return None, "ambiguo"
    candidatos = por_nombre.get("~" + nucleo(texto))
    if candidatos and len(candidatos) == 1:
        return candidatos[0], "nucleo"
    if candidatos:
        return None, "ambiguo"
    return None, "sin_match"


def cargar_senales(client) -> list[dict[str, str]]:
    rows = client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    uris = sorted({str(r["s"]) for r in rows})
    senales: list[dict[str, str]] = []
    for start in range(0, len(uris), 60):
        chunk = uris[start:start + 60]
        values = " ".join(f"<{u}>" for u in chunk)
        props: dict[str, dict[str, str]] = {}
        for row in client.select(f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o }}"):
            subject = str(row["s"])
            predicate = str(row["p"]).replace(ONTO, "")
            props.setdefault(subject, {})[predicate] = str(row["o"])
        for uri in chunk:
            found = props.get(uri, {})
            senales.append({
                "uri": uri,
                "id": uri.rsplit("/", 1)[-1],
                "organismo": found.get("organismo_nombre", ""),
                "proveedor": found.get("proveedor_nombre", ""),
            })
    return senales


def main() -> int:
    parser = argparse.ArgumentParser(description="Vincular señales con su organismo y proveedor")
    parser.add_argument("--apply", action="store_true", help="escribe las aristas (default: dry run)")
    args = parser.parse_args()

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    client = store.client

    rutas_org, orgs = cargar(client, "Organismo")
    rutas_prov, provs = cargar(client, "Proveedor")
    senales = cargar_senales(client)
    print(f"organismos: {len(rutas_org)} · proveedores: {len(rutas_prov)} · señales: {len(senales)}")

    triples: list[str] = []
    modos: Counter = Counter()
    con_org = con_prov = 0

    for senal in senales:
        org, org_modo = resolver(senal["organismo"], rutas_org, orgs)
        prov, prov_modo = resolver(senal["proveedor"], rutas_prov, provs)
        modos[(org_modo, prov_modo)] += 1

        if org:
            triples.append(
                f"<{senal['uri']}> <{relation_uri('SOBRE_ORGANISMO')}> "
                f"<{node_uri('Organismo', org)}> ."
            )
            con_org += 1
        if prov:
            triples.append(
                f"<{senal['uri']}> <{relation_uri('SOBRE_PROVEEDOR')}> "
                f"<{node_uri('Proveedor', prov)}> ."
            )
            con_prov += 1

    vinculadas = sum(1 for k, n in modos.items() if k[0] not in (None, "sin_match", "ambiguo", "vacio")
                     or k[1] not in (None, "sin_match", "ambiguo", "vacio") for _ in range(n))
    print(f"aristas a escribir: {len(triples)} (organismo {con_org} · proveedor {con_prov})")
    print(f"señales que quedan vinculadas: {vinculadas}/{len(senales)}")
    print("modos (organismo, proveedor):", dict(modos.most_common(6)))

    if not args.apply:
        print("\nDRY RUN — nada escrito. Repetir con --apply.")
        for triple in triples[:4]:
            print("  ", triple)
        return 0

    written = 0
    for start in range(0, len(triples), 400):
        batch = triples[start:start + 400]
        client.insert(batch)
        written += len(batch)
        print(f"  escritas {written}/{len(triples)}", flush=True)
    print(f"listo: {written} aristas nuevas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
