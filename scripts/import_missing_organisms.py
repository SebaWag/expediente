"""Importa al grafo los registros de organismos que faltaban.

Por qué existe
--------------

El grafo tenía 4,95 M órdenes de 1.400 organismos. El corpus agregado (`compras_5y.jsonl`,
10.302.746 registros) contiene **1.617.619 registros de 700 organismos que nunca se importaron**:
Universidad de Chile, CONAF, JUNJI, SAG, IND, SENAME, DGAC, JUNAEB y sus universidades.

Eso explica el síntoma que levantó Sebastián: preguntar por JUNAEB devolvía "no existe". No era el
agente: el organismo no estaba en el grafo, aunque sus 11.121 registros sí estaban en el corpus.

Qué escribe
-----------

Por cada registro de un organismo ausente:

    :Organismo  nombre, rut_norm, region
    :Proveedor  razon_social, rut_norm
    :OrdenCompra  codigo, monto_clp, fecha, procedencia, descripcion, estado, moneda,
                  sector, region_compra, items
    :Organismo -EMITE-> :OrdenCompra -ADJUDICADA_A-> :Proveedor

El vocabulario es el del grafo existente, copiado de un nodo real (la fecha va en `dd-mm-yyyy`,
no ISO: si se escribe distinto, las consultas por rango dejan de encontrarla).

Garantías
---------

* **Solo agrega.** No borra ni reescribe nodos existentes: filtra por `organismo_rut` ausente.
* **Idempotente.** Los URIs se derivan del código y del RUT; re-ejecutar no duplica.
* **Por lotes, con progreso.** Si se corta, se vuelve a correr y sigue.
* **No inventa.** Un registro sin RUT de organismo o de proveedor se cuenta y se salta.

Uso
---

    python3 scripts/import_missing_organisms.py --limit 2000        # prueba
    python3 scripts/import_missing_organisms.py --apply             # todo
"""

from __future__ import annotations

import argparse
import json
import re
import zlib
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/seba/expediente")

from expediente.graph.sparql import SparqlClient                    # noqa: E402

ONTO = "urn:expediente:onto/"
NODE = "urn:expediente:node/"
REL = f"{ONTO}rel/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

FUENTE = Path("/home/seba/expediente/out/compras_5y.jsonl")
LOTE = 6000  # triples por petición: medido contra el endpoint, 6 k tarda ~0,6 s


#: Un RUT chileno, con o sin puntos, con guion opcional. Los proveedores extranjeros traen
#: identificadores como "ES B-84314699": un espacio en el camino del URI lo vuelve inválido y el
#: motor responde "mismatched input <". Son 8 de 1,6 M de registros, y quedan fuera del modelo de
#: RUT chileno, así que se cuentan y se saltan en vez de inventarles una clave.
RUT_OK = re.compile(r"^[0-9]{1,2}\.?[0-9]{3}\.?[0-9]{3}-?[0-9Kk]$")


def rut_utilizable(valor: str) -> bool:
    return bool(RUT_OK.match(str(valor or "").strip()))


def lit(valor) -> str:
    """Un literal escapado. Los saltos de línea y comillas vienen en las descripciones."""
    texto = str(valor if valor is not None else "")
    texto = texto.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{texto}"'


def fecha_grafo(iso: str) -> str | None:
    """`2021-06-25` → `25-06-2021 0:00:00`, que es como están las fechas en el grafo."""
    partes = str(iso or "").split("-")
    if len(partes) != 3 or len(partes[0]) != 4:
        return None
    return f"{partes[2]}-{partes[1]}-{partes[0]} 0:00:00"


def main() -> int:
    parser = argparse.ArgumentParser(description="Importar organismos ausentes del corpus")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="máximo de registros a procesar")
    parser.add_argument("--url", default="http://127.0.0.1:1235")
    parser.add_argument("--checkpoint", default="/tmp/import_organismos.ckpt")
    parser.add_argument("--targets", default=None,
                        help="archivo con un RUT de organismo por línea: importar solo esos")
    parser.add_argument("--shard", default=None,
                        help="i/N — procesar solo los organismos cuyo hash cae en el shard i")
    args = parser.parse_args()

    client = SparqlClient(args.url)

    #: Sharding por hash del RUT, no por posición en el archivo: el mismo organismo cae siempre
    #: en el mismo shard, así que dos procesos nunca escriben el mismo nodo y un corte se reanuda
    #: sin coordinación entre ellos.
    shard, total_shards = (None, 1)
    if args.shard:
        parte, total = args.shard.split("/")
        shard, total_shards = int(parte), int(total)
        if not 0 <= shard < total_shards:
            raise SystemExit("--shard debe ser i/N con 0 <= i < N")
    #: Con --targets manda la lista fija y se ignora el estado del grafo. Es la única forma
    #: correcta de reanudar: el filtro "organismo ausente del grafo" se cumple a medias cuando un
    #: organismo quedó importado en parte, y entonces el script lo salta y termina en 0 registros
    #: (medido: pasó exactamente eso con 665 organismos a medio importar).
    objetivo: set[str] | None = None
    if args.targets:
        objetivo = {line.strip() for line in Path(args.targets).read_text(encoding="utf-8").splitlines() if line.strip()}
        print(f"objetivos desde {args.targets}: {len(objetivo):,} organismos", flush=True)

    #: El corte se reanuda por organismo: un organismo importado a medias dejaría su historia
    #: incompleta y el filtro por "ausente del grafo" lo daría por hecho. El checkpoint se
    #: escribe después de cada lote confirmado.
    checkpoint = Path(args.checkpoint)
    hechos: set[str] = set()
    if checkpoint.exists():
        hechos = {line.strip() for line in checkpoint.read_text(encoding="utf-8").splitlines() if line.strip()}
        print(f"reanudando: {len(hechos):,} organismos ya importados", flush=True)

    existentes = {
        str(fila["k"])
        for fila in client.select(
            f"SELECT ?k WHERE {{ ?x <{RDF_TYPE}> <{ONTO}Organismo> . ?x <{ONTO}rut_norm> ?k }}"
        )
    } | hechos
    print(f"organismos en el grafo: {len(existentes)}", flush=True)

    buffer: list[str] = []
    leidos = 0
    escritos = 0
    sin_rut = 0
    empezado = time.time()
    organismos_nuevos: set[str] = set()
    proveedores_nuevos: set[str] = set()

    def escribir(triples: list[str], profundidad: int = 0) -> int:
        """Inserta; si el motor rechaza el lote, lo parte hasta aislar el triple inválido.

        Un lote de 6.000 tripletas que falla no dice cuál está mal. Partirlo a la mitad y
        reintentar cuesta unos segundos y convierte un fallo fatal en un triple descartado
        con nombre y apellido.
        """
        if not triples:
            return 0

        #: Dos clases de fallo, y confundirlas cuesta datos.
        #:
        #:   HTTP 400  → el lote tiene un triple que el parser no acepta. Se parte hasta aislarlo.
        #:   otro      → red, túnel, 5xx, reinicio del motor. NO es un dato malo: se reintenta
        #:               con espera creciente y, si persiste, se levanta para que el supervisor
        #:               reanude desde el checkpoint.
        #:
        #: La primera versión trataba todo como "triple inválido" y habría descartado 6.000
        #: tripletas por un corte de red de dos segundos.
        for intento in range(5):
            try:
                client.insert(triples)
                return len(triples)
            except Exception as error:  # noqa: BLE001
                es_sintaxis = "HTTP 400" in str(error)
                if es_sintaxis:
                    if len(triples) == 1:
                        with open("/tmp/import_rechazados.txt", "a", encoding="utf-8") as malos:
                            malos.write(f"{error} ||| {triples[0][:300]}\n")
                        return 0
                    medio = len(triples) // 2
                    return (escribir(triples[:medio], profundidad + 1)
                            + escribir(triples[medio:], profundidad + 1))
                espera = 2 ** intento
                print(f"  reintento {intento + 1}/5 en {espera}s: {str(error)[:90]}", flush=True)
                time.sleep(espera)
        raise RuntimeError("el motor no aceptó el lote tras 5 intentos; el supervisor debe reanudar")

    def volcar(forzar: bool = False) -> None:
        nonlocal buffer, escritos
        if not buffer or (len(buffer) < LOTE and not forzar):
            return
        #: Los organismos de este lote quedan como hechos solo cuando el lote se confirmó.
        for triple in buffer:
            if "/Organismo/" in triple and f"<{ONTO}nombre>" in triple:
                rut = triple.split("/Organismo/", 1)[1].split(">", 1)[0]
                if rut not in hechos:
                    hechos.add(rut)
                    with checkpoint.open("a", encoding="utf-8") as ck:
                        ck.write(rut + "\n")
        escritos += escribir(buffer)
        buffer = []

    with FUENTE.open(encoding="utf-8", errors="ignore") as fh:
        for linea in fh:
            if not linea.strip():
                continue
            try:
                d = json.loads(linea)
            except ValueError:
                continue

            rut_org = (d.get("organismo_rut") or "").strip()
            rut_prov = (d.get("proveedor_rut") or "").strip()
            codigo = (d.get("codigo") or "").strip()
            if not rut_org or not rut_prov or not codigo:
                continue
            if objetivo is not None:
                #: La lista fija manda: se reimportan los registros ya escritos (RDF es un
                #: conjunto: los duplicados no hacen nada) antes que dejar un organismo a medias.
                if rut_org not in objetivo:
                    continue
            elif rut_org in existentes:
                continue
            if shard is not None and zlib.crc32(rut_org.encode("utf-8")) % total_shards != shard:
                continue
            if not rut_utilizable(rut_org) or not rut_utilizable(rut_prov):
                sin_rut += 1
                continue
            fecha = fecha_grafo(d.get("fecha"))
            if not fecha:
                continue

            leidos += 1
            org = f"<{NODE}Organismo/{rut_org}>"
            prov = f"<{NODE}Proveedor/{rut_prov}>"
            oc = f"<{NODE}OrdenCompra/{codigo}>"

            if rut_org not in organismos_nuevos:
                organismos_nuevos.add(rut_org)
                buffer.append(f"{org} <{RDF_TYPE}> <{ONTO}Organismo> .")
                buffer.append(f"{org} <{ONTO}rut_norm> {lit(rut_org)} .")
                buffer.append(f"{org} <{ONTO}nombre> {lit(d.get('organismo'))} .")
                if d.get("region_compra"):
                    buffer.append(f"{org} <{ONTO}region> {lit(d.get('region_compra'))} .")
            if rut_prov not in proveedores_nuevos:
                proveedores_nuevos.add(rut_prov)
                buffer.append(f"{prov} <{RDF_TYPE}> <{ONTO}Proveedor> .")
                buffer.append(f"{prov} <{ONTO}rut_norm> {lit(rut_prov)} .")
                buffer.append(f"{prov} <{ONTO}razon_social> {lit(d.get('proveedor'))} .")

            buffer.append(f"{oc} <{RDF_TYPE}> <{ONTO}OrdenCompra> .")
            buffer.append(f"{oc} <{ONTO}codigo> {lit(codigo)} .")
            buffer.append(f"{oc} <{ONTO}monto_clp> {lit(d.get('monto_total_clp') or 0)} .")
            buffer.append(f"{oc} <{ONTO}fecha> {lit(fecha)} .")
            buffer.append(f"{oc} <{ONTO}procedencia> {lit(d.get('procedencia'))} .")
            buffer.append(f"{oc} <{ONTO}descripcion> {lit((d.get('descripcion') or d.get('nombre') or '')[:300])} .")
            buffer.append(f"{oc} <{ONTO}estado> {lit(d.get('estado'))} .")
            buffer.append(f"{oc} <{ONTO}moneda> {lit(d.get('moneda') or 'CLP')} .")
            if d.get("sector"):
                buffer.append(f"{oc} <{ONTO}sector> {lit(d.get('sector'))} .")
            if d.get("region_compra"):
                buffer.append(f"{oc} <{ONTO}region_compra> {lit(d.get('region_compra'))} .")
            if d.get("items"):
                buffer.append(f"{oc} <{ONTO}items> {lit(d.get('items'))} .")
            buffer.append(f"{org} <{REL}EMITE> {oc} .")
            buffer.append(f"{oc} <{REL}ADJUDICADA_A> {prov} .")

            if args.apply:
                volcar()
                if leidos % 5000 == 0:
                    tasa = leidos / max(1e-6, time.time() - empezado)
                    print(f"  {leidos:,} registros · {escritos:,} tripletas · {tasa:.0f} reg/s", flush=True)

            if args.limit and leidos >= args.limit:
                break

    if args.apply:
        volcar(forzar=True)

    print(f"\nregistros de organismos ausentes: {leidos:,}")
    print(f"organismos nuevos: {len(organismos_nuevos):,} · proveedores nuevos: {len(proveedores_nuevos):,}")
    print(f"tripletas escritas: {escritos:,}")
    if not args.apply:
        print("\nDRY RUN — nada escrito. Repetir con --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
