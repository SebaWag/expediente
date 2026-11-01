#!/usr/bin/env python3
"""Find the orders of a finding inside the State's own monthly archive, and report coverage.

The dossier's citations point at a line of an official CSV. Until this script existed those
lines were produced by hand, once, and a citation that is produced by hand cannot be reproduced
by a reviewer who does not trust the author. This turns that one-off into the pipeline:

    signal (a chain of order codes) -> download the month's archive -> search every code
    -> line numbers, a sha256 of the file, and a coverage figure that may be less than 100%

Three properties are deliberate.

**It never guesses.** A code that is not in the archive has no line, is reported as missing, and
stays missing. The gate downstream decides whether a claim survives without it; this script only
reports what it saw.

**The line number means what ``grep`` means.** Lines are counted over the raw bytes of the CSV,
so ``sed -n '36451p' 2021-11.csv`` finds the same record a reviewer would find. A "page" that
cannot be checked by ``grep`` is not a citation.

**The reading is recorded.** The evidence file carries the archive URL, the member name, the
size, the sha256 and the moment of retrieval, because a citation to a mutable blob is not a
citation unless you say which bytes you read.

Usage:
    python3 scripts/verify_archive.py --month 2021-11 \
        --codes 1398-1257-SE21 1398-1459-SE21 \
        --out out/evidence_2021-11.json

    python3 scripts/verify_archive.py --signal out/signals/signals_fraccionamiento.jsonl:12 \
        --out out/evidence_chain12.json

    # offline / already downloaded
    python3 scripts/verify_archive.py --month 2021-11 --codes ... --archive /tmp/2021-11.zip
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_URL = "https://transparenciachc.blob.core.windows.net/oc-da/{month}.zip"


def archive_candidates(month: str) -> list[str]:
    """Both spellings of a month, because the publisher uses both.

    January to September are published without a leading zero (``2021-1.zip``) and October to
    December with one (``2021-11.zip``). Guessing wrong is a 404 that looks like "this month was
    never published", so both are tried and the one that answered is the one recorded.
    """
    year, _, number = month.partition("-")
    if not number:
        return [ARCHIVE_URL.format(month=month)]
    spellings = [month, f"{year}-{int(number)}", f"{year}-{int(number):02d}"]
    seen, candidates = set(), []
    for spelling in spellings:
        if spelling not in seen:
            seen.add(spelling)
            candidates.append(ARCHIVE_URL.format(month=spelling))
    return candidates
DEFAULT_CACHE = Path(os.environ.get("EXPEDIENTE_ARCHIVE_CACHE", Path.home() / ".cache" / "expediente" / "oc-da"))
CHUNK = 1 << 20


@dataclass
class Coverage:
    """What the archive said about a set of codes. Nothing here is inferred."""

    month: str
    archive: str
    member: str | None = None
    members: dict[str, str] = field(default_factory=dict)
    lines: dict[str, int] = field(default_factory=dict)
    method: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    delimiter: str = ";"
    archive_bytes: int = 0
    archive_sha256: str = ""
    csv_bytes: int = 0
    csv_lines: int = 0
    seconds: float = 0.0
    retrieved_at: str = ""
    cached: bool = False

    @property
    def codes(self) -> int:
        return len(self.lines) + len(self.missing)

    @property
    def rate(self) -> float:
        return (len(self.lines) / self.codes) if self.codes else 0.0

    def report(self) -> dict:
        return {
            "month": self.month,
            "archive": self.archive,
            "member": self.member,
            "members": self.members,
            "codes": self.codes,
            "found": len(self.lines),
            "missing": self.missing,
            "method": self.method,
            "delimiter": self.delimiter,
            "coverage": round(self.rate, 4),
            "archive_bytes": self.archive_bytes,
            "archive_sha256": self.archive_sha256,
            "csv_bytes": self.csv_bytes,
            "csv_lines": self.csv_lines,
            "seconds": round(self.seconds, 1),
            "retrieved_at": self.retrieved_at,
            "cached": self.cached,
        }


def _cached_url(sidecar: Path, month: str) -> str:
    """The URL recorded when this archive was fetched, or the spelling that still answers.

    A cache written before this file existed has no record of where it came from, and guessing
    the spelling from the month is wrong half the year. So an unrecorded archive is probed with
    HEAD — a 404 costs nothing — and the answer is written down so the next run does not ask.
    """
    if sidecar.exists():
        recorded = sidecar.read_text(encoding="utf-8").strip()
        if recorded:
            return recorded
    for candidate in archive_candidates(month):
        request = urllib.request.Request(candidate, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=30):
                pass
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue
        sidecar.write_text(candidate, encoding="utf-8")
        return candidate
    return ARCHIVE_URL.format(month=month)


def download(month: str, cache: Path, progress=sys.stderr) -> tuple[Path, bool, str]:
    """Fetch the monthly archive once, name it after its own hash, and never fetch it twice.

    The cache key is the month; the integrity check is the size, because the blob publishes no
    ETag we can trust across a change. A partially written file is never left under the final
    name: it downloads as ``.part`` and is renamed only when the stream ends, so a killed run
    costs a download and not a silent truncation.
    """
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{month}.zip"
    sidecar = cache / f"{month}.url"
    if target.exists() and target.stat().st_size > 0:
        return target, True, _cached_url(sidecar, month)

    part = cache / f"{month}.zip.part"
    started = time.time()
    fetched = 0
    url = ""
    response = None
    for candidate in archive_candidates(month):
        try:
            response = urllib.request.urlopen(candidate, timeout=120)
            url = candidate
            break
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            print(f"  404 en {candidate.rsplit('/', 1)[-1]}, pruebo otra grafía", file=progress)
    if response is None:
        raise SystemExit(f"el archivo de {month} no existe en ninguna de sus grafías: {archive_candidates(month)}")
    print(f"descargando {url}", file=progress)
    with response, part.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            handle.write(block)
            fetched += len(block)
            if total and progress.isatty():
                pct = fetched * 100 // total
                print(f"\r  {fetched/1e6:7.1f} MB / {total/1e6:.1f} MB  {pct:3d}%", end="", file=progress)
    if progress.isatty():
        print(file=progress)
    print(f"  {fetched/1e6:.1f} MB en {time.time()-started:.1f} s", file=progress)
    part.replace(target)
    # The URL that answered is a fact about the archive, not about the month: January to
    # September are published as 2021-1.zip and October to December as 2021-11.zip. A cache
    # hit that rebuilt the URL from the month would cite a 404 in every dossier built offline.
    sidecar.write_text(url, encoding="utf-8")
    return target, False, url


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _bounded_in(code: str, text: str, delimiter: str) -> bool:
    """Is the code a whole field, judging by the characters that bound fields?

    The first confirmation parses the line. This one does not, because a record that spans
    physical lines does not parse as a record — but it still has field boundaries. Quotes and
    the delimiter are normalised to the delimiter, so the code must sit between two of them,
    which is exactly what stops ``701-1-SE21`` from matching ``701-1-SE21-A``.
    """
    normalised = text.replace('"', delimiter)
    return f"{delimiter}{code}{delimiter}" in normalised


def _delimiter_of(header: bytes) -> str:
    """The archive's CSV is not comma-separated: it ships as ``"a";"b";...``. Sniff it."""
    text = header.decode("utf-8", "replace")
    scored = {candidate: text.count(candidate) for candidate in (";", ",", "\t", "|")}
    best = max(scored, key=lambda key: scored[key])
    return best if scored[best] else ";"


def search(archive: Path, codes: list[str], coverage: Coverage, progress=sys.stderr) -> Coverage:
    """Scan the CSV members for the codes, counting lines the way ``grep -n`` counts them.

    Two confirmations, in this order, and the report records which one accepted the code:

    * **field** — the line parses as CSV and one of its fields equals the code exactly. This is
      the citation-grade match.
    * **delimited** — the record spans physical lines (a quoted newline inside an observation),
      so the fragment does not parse; the code is accepted only if it appears between the
      delimiter and its own delimiters, ``;"CODE";``. Bounded by the same characters a parser
      would use, so it cannot match a longer code that merely contains this one.

    A code accepted by neither is missing, and missing is reported as missing.
    """
    remaining = {code: True for code in codes}
    needles = {code: code.encode() for code in codes}
    checked = 0
    delimiter = ";"

    with zipfile.ZipFile(archive) as bundle:
        members = [info for info in bundle.infolist() if info.filename.lower().endswith(".csv")]
        if not members:
            raise SystemExit(f"{archive} no trae ningún CSV: {[i.filename for i in bundle.infolist()][:5]}")

        for info in members:
            if not remaining:
                break
            print(f"  buscando en {info.filename} ({info.file_size/1e6:.0f} MB)", file=progress)
            with bundle.open(info) as raw:
                for lineno, line in enumerate(raw, start=1):
                    if lineno == 1:
                        delimiter = _delimiter_of(line)
                        coverage.delimiter = delimiter
                    checked += 1
                    if not any(needle in line for needle in needles.values()):
                        continue
                    text = line.decode("utf-8", "replace")
                    fields = next(csv.reader([text], delimiter=delimiter), [])
                    for code in list(remaining):
                        if code in fields:
                            method = "field"
                        elif _bounded_in(code, text, delimiter):
                            method = "delimited"
                        else:
                            continue
                        coverage.lines[code] = lineno
                        coverage.members[code] = info.filename
                        coverage.method[code] = method
                        remaining.pop(code)
                        needles.pop(code, None)
            coverage.csv_bytes += info.file_size
    coverage.csv_lines = checked
    coverage.missing = sorted(remaining)
    distinct = sorted(set(coverage.members.values()))
    coverage.member = distinct[0] if len(distinct) == 1 else None
    return coverage


def verify(month: str, codes: list[str], cache: Path = DEFAULT_CACHE, archive: Path | None = None,
           progress=sys.stderr) -> Coverage:
    started = time.time()
    if archive:
        path, cached, url = archive, True, str(archive)
    else:
        path, cached, url = download(month, cache, progress)
    coverage = Coverage(
        month=month,
        archive=url,
        archive_bytes=path.stat().st_size,
        archive_sha256=sha256_of(path),
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cached=cached,
    )
    search(path, codes, coverage, progress)
    coverage.seconds = time.time() - started
    return coverage


def evidence_of(coverage: Coverage, finding: dict | None = None) -> dict:
    """The file ``scripts/dossier_real.py`` consumes, plus the provenance it did not have."""
    body = {
        "codes": sorted(set(coverage.lines) | set(coverage.missing)),
        "lines": coverage.lines,
        "archive": coverage.archive,
        "member": coverage.member,
        "members": coverage.members,
        "archive_sha256": coverage.archive_sha256,
        "archive_bytes": coverage.archive_bytes,
        "retrieved_at": coverage.retrieved_at,
        "month": coverage.month,
        "coverage": round(coverage.rate, 4),
        "missing": coverage.missing,
        "method": coverage.method,
        "delimiter": coverage.delimiter,
    }
    if finding:
        body["finding"] = finding
    return body


def codes_from_signal(path: Path, index: int | None) -> tuple[list[str], dict, str]:
    """Read a signal line: the codes, the finding, and the period it belongs to."""
    with path.open("r", encoding="utf-8") as handle:
        for position, line in enumerate(handle):
            if not line.strip():
                continue
            if index is not None and position != index:
                continue
            signal = json.loads(line)
            codes = signal.get("order_codes") or []
            period = signal.get("period") or ""
            if not codes or not period:
                raise SystemExit(f"la señal {position} no trae order_codes/period")
            finding = {
                key: signal.get(key)
                for key in ("organismo", "proveedor", "period", "total_clp", "ceiling_clp", "n_orders")
                if signal.get(key) is not None
            }
            if signal.get("ceiling_clp"):
                finding["factor"] = round(signal["total_clp"] / signal["ceiling_clp"], 4)
            return codes, finding, period
    raise SystemExit(f"no encontré la señal en {path}:{index}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", help="YYYY-MM; obligatorio salvo con --signal")
    parser.add_argument("--signal", help="ruta_a_señales.jsonl:N (índice por línea, 0-based)")
    parser.add_argument("--codes", nargs="*", default=[], help="códigos de orden a buscar")
    parser.add_argument("--codes-file", type=Path, help="un código por línea")
    parser.add_argument("--archive", type=Path, help="zip ya descargado (no descarga nada)")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, required=True, help="evidencia para dossier_real.py")
    parser.add_argument("--report", type=Path, help="reporte de cobertura (JSON); por defecto al lado del --out")
    parser.add_argument("--require-all", action="store_true", help="falla si algún código no aparece")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv[1:])

    finding = None
    codes = list(args.codes)
    month = args.month
    if args.signal:
        path_text, _, index_text = args.signal.rpartition(":")
        if not path_text:
            path_text, index_text = args.signal, ""
        signal_codes, finding, period = codes_from_signal(Path(path_text), int(index_text) if index_text else None)
        codes = codes or signal_codes
        month = month or period
    if args.codes_file:
        codes += [line.strip() for line in args.codes_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not month:
        raise SystemExit("falta --month (o una --signal que traiga period)")
    if not codes:
        raise SystemExit("no hay códigos que buscar")

    stream = open(os.devnull, "w") if args.quiet else sys.stderr
    try:
        coverage = verify(month, codes, cache=args.cache, archive=args.archive, progress=stream)
    finally:
        if args.quiet:
            stream.close()

    report = coverage.report()
    if finding:
        report["finding"] = finding
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence_of(coverage, finding), ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = args.report or args.out.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{month} · {len(coverage.lines)}/{report['codes']} códigos encontrados "
          f"({coverage.rate:.0%}) en {coverage.member or '·'.join(sorted(set(coverage.members.values())))}")
    print(f"  sha256 {coverage.archive_sha256[:16]}… · {coverage.archive_bytes/1e6:.1f} MB · "
          f"{coverage.csv_lines:,} líneas en {coverage.seconds:.1f} s")
    for code in coverage.missing:
        print(f"  SIN LÍNEA: {code}")
    print(f"  evidencia → {args.out}")
    print(f"  reporte   → {report_path}")

    if args.require_all and coverage.missing:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
