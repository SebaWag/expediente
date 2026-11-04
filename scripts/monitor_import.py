#!/usr/bin/env python3
"""Vigila el import de organismos y deja el estado final medido en disco.

Por qué existe
--------------
El import escribe a ~200 registros/s sobre 1.617.619 registros: son ~2,3 h. Nadie va a estar
mirando la terminal a las 04:30, y el número final es el que va al README. Este monitor no
adivina: cuando el proceso termina, consulta el grafo y escribe los conteos medidos.

Reglas
------
* **No consulta el grafo mientras el import escribe.** Solo hace `tail` del log (I/O local) y
  comprueba el proceso. Las consultas SPARQL se ejecutan una sola vez, al final.
* **No reinicia nada.** Si el proceso muere antes de terminar, lo reporta y sale.
* Deja dos archivos: `out/import_monitor.log` (cronológico) y `out/import_final_counts.json`
  (medido, listo para el README).

Uso
---
    nohup python3 scripts/monitor_import.py --pid 149524 \\
        --url http://127.0.0.1:1240 > out/import_monitor.log 2>&1 &
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

LOG = Path("/tmp/import_org.log")
ROOT = Path(__file__).resolve().parent.parent
FINAL = ROOT / "out" / "import_final_counts.json"

#: Lo que el corpus dice que debería haber, para comparar. Medido en out/aggregate_stats_*.json
#: (6.646.814 órdenes OC + 3.655.932 de Compra Ágil) y en el export de NTriples.
CORPUS = {"ordenes": 10_302_746, "organismos": 2_065, "proveedores": 191_354}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # Un zombie responde a kill(0); se comprueba el estado real.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return False


def last_progress() -> str:
    if not LOG.exists():
        return "(sin log)"
    try:
        with LOG.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4000))
            lines = [ln for ln in fh.read().decode("utf-8", "ignore").splitlines() if ln.strip()]
        return lines[-1].strip() if lines else "(log vacío)"
    except OSError as exc:
        return f"(no se pudo leer el log: {exc})"


def sparql(url: str, query: str, timeout: int = 120) -> str | None:
    data = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/sparql",
        data=data,
        headers={"Accept": "application/sparql-results+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
        return payload["results"]["bindings"][0]["n"]["value"]
    except Exception as exc:  # noqa: BLE001 - el monitor no debe morir por una consulta
        log(f"  ! consulta falló: {exc}")
        return None


def count(url: str, rdf_type: str) -> int | None:
    value = sparql(url, f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s a <urn:expediente:onto/{rdf_type}> }}")
    return int(value) if value is not None else None


def measure(url: str) -> dict[str, object]:
    log("midiendo el grafo (una sola vez, el import ya terminó)")
    result: dict[str, object] = {"medido_en": now(), "endpoint": url}
    for label, rdf_type in (
        ("organismos", "Organismo"),
        ("ordenes", "OrdenCompra"),
        ("proveedores", "Proveedor"),
        ("documentos", "Documento"),
        ("senales", "Senal"),
    ):
        result[label] = count(url, rdf_type)
        log(f"  {label}: {result[label]}")
    total = sparql(url, "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", timeout=180)
    result["tripletas"] = int(total) if total is not None else None
    log(f"  tripletas: {result['tripletas']}")
    result["corpus"] = CORPUS
    if isinstance(result.get("ordenes"), int):
        result["cobertura_ordenes"] = round(result["ordenes"] / CORPUS["ordenes"], 4)
    if isinstance(result.get("organismos"), int):
        result["cobertura_organismos"] = round(result["organismos"] / CORPUS["organismos"], 4)
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pid", type=int, required=True, help="pid del import")
    parser.add_argument("--url", default="http://127.0.0.1:1240")
    parser.add_argument("--interval", type=int, default=60, help="segundos entre comprobaciones")
    parser.add_argument("--out", type=Path, default=FINAL)
    args = parser.parse_args(argv[1:])

    log(f"vigilando pid {args.pid} · intervalo {args.interval}s · log {LOG}")
    log(f"último progreso: {last_progress()}")

    started = time.monotonic()
    while alive(args.pid):
        time.sleep(args.interval)
        log(f"vivo · {last_progress()}")

    elapsed = int(time.monotonic() - started)
    log(f"el proceso {args.pid} terminó tras {elapsed // 60} min de vigilancia")
    log(f"última línea del log: {last_progress()}")

    result = measure(args.url)
    #: No se interpreta el final: se deja la última línea tal cual, para que quien lea
    #: el JSON decida. Un grep de "listo" sobre un log en español es una fuente de falsos
    #: positivos, y este archivo alimenta el README.
    result["ultima_linea_log"] = last_progress()
    result["minutos_vigilados"] = elapsed // 60

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"escrito {args.out}")
    log("RESUMEN " + json.dumps({k: v for k, v in result.items() if k != "corpus"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
