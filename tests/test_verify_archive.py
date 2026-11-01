"""The citation is only a citation if a stranger can open the file and find the line.

`scripts/verify_archive.py` is the piece that turns a chain of order codes into line numbers
inside the State's own monthly archive. Everything downstream inherits its honesty: if it
invents a line, the dossier cites a page that does not exist, and the whole artifact — the one
claim in this repository that separates it from a dashboard — becomes theatre.

So these tests do not check that it "runs". They check the three properties the docstring
promises, each of which broke at least once on the way here:

1. **Found means found, missing means missing.** A code that is not in the archive has no line.
2. **The line number is the line `grep -n` would print.** Counted over physical lines, from 1.
3. **The archive's real format is handled.** It is semicolon-separated, some records span
   physical lines, and January is published as `2021-1.zip` while November is `2021-11.zip`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import verify_archive  # noqa: E402

HEADER = '"ID";"Codigo";"Nombre";"MontoTotalOC_PesosChilenos"'


def build_archive(path: Path, rows: list[str], member: str = "2021-1.csv") -> Path:
    """A tiny archive with the same shape as the real one: ``;`` delimiter, quoted fields."""
    body = "\n".join([HEADER] + rows) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(member, body)
    return path


def row(order_id: int, code: str, name: str = "COMPRA", amount: int = 1_000_000) -> str:
    return f'{order_id};"{code}";"{name}";{amount}'


def test_every_code_that_is_present_gets_a_line_and_every_other_one_does_not(tmp_path):
    archive = build_archive(
        tmp_path / "2021-1.zip",
        [row(1, "701-1-SE21"), row(2, "701-2-SE21"), row(3, "701-3-SE21")],
    )
    coverage = verify_archive.verify("2021-1", ["701-1-SE21", "701-9-SE21"], archive=archive)

    assert coverage.lines == {"701-1-SE21": 2}
    assert coverage.missing == ["701-9-SE21"]
    assert coverage.rate == 0.5
    assert coverage.method["701-1-SE21"] == "field"


def test_the_line_number_is_the_one_grep_would_print(tmp_path):
    # The code sits on the fifth physical line: header, three other orders, then this one.
    rows = [row(1, "A-1"), row(2, "A-2"), row(3, "A-3"), row(4, "A-4-TARGET")]
    archive = build_archive(tmp_path / "2021-1.zip", rows)

    coverage = verify_archive.verify("2021-1", ["A-4-TARGET"], archive=archive)

    assert coverage.lines["A-4-TARGET"] == 5
    # And the file really says so, read the way a reviewer would read it.
    with zipfile.ZipFile(archive) as bundle:
        physical = bundle.read("2021-1.csv").decode("utf-8").splitlines()
    assert "A-4-TARGET" in physical[4]


def test_a_code_inside_a_longer_code_is_not_a_match(tmp_path):
    # "701-1-SE21" is a strict prefix of "701-1-SE21-A". A substring search would report the
    # second line as evidence for the first, which is how a citation ends up pointing at a
    # different order that happens to look similar.
    archive = build_archive(
        tmp_path / "2021-1.zip",
        [row(1, "701-1-SE21-A"), row(2, "701-1-SE21")],
    )

    coverage = verify_archive.verify("2021-1", ["701-1-SE21"], archive=archive)

    assert coverage.lines == {"701-1-SE21": 3}


def test_a_code_is_not_matched_inside_a_longer_code_or_freetext(tmp_path):
    # The fallback has to be as strict as the parser it backs up: bounded by quotes or the
    # delimiter on both sides, never a substring of a longer code or of a description.
    assert verify_archive._bounded_in("701-1-SE21", 'x;701-1-SE21";9', ";") is True
    assert verify_archive._bounded_in("701-1-SE21", 'x;701-1-SE21-A";9', ";") is False
    assert verify_archive._bounded_in("701-1-SE21", "x701-1-SE21y", ";") is False


def test_delimiter_is_sniffed_from_the_header_because_it_is_not_a_comma(tmp_path):
    # The first version of the verifier split on "," and found 0 of 9 real codes: every line
    # parsed as a single field. The archive ships "a";"b";... with semicolons.
    archive = build_archive(tmp_path / "2021-1.zip", [row(1, "1467-17-SE21")])

    coverage = verify_archive.verify("2021-1", ["1467-17-SE21"], archive=archive)

    assert coverage.delimiter == ";"
    assert coverage.lines == {"1467-17-SE21": 2}


def test_a_record_split_across_physical_lines_is_still_cited(tmp_path):
    # Observations carry embedded newlines, so one record occupies several physical lines. When
    # the code sits on the *first* line the fragment still parses; when it sits after an embedded
    # newline it does not, and it is accepted only between its own delimiters.
    first_line = '7;"1467-18-SE21";"NOMBRE\nCON SALTO";900000'
    after_the_newline = '7;"NOMBRE\nCON SALTO;1467-20-SE21";900000'
    archive = build_archive(
        tmp_path / "2021-1.zip",
        [first_line, row(8, "1467-19-SE21"), after_the_newline],
    )

    coverage = verify_archive.verify("2021-1", ["1467-18-SE21", "1467-20-SE21"], archive=archive)

    assert coverage.lines["1467-18-SE21"] == 2
    assert coverage.method["1467-18-SE21"] == "field"
    # Physical line 6: the record starts on 5 ("7;\"NOMBRE"), and the code is on the line after.
    assert coverage.lines["1467-20-SE21"] == 6
    assert coverage.method["1467-20-SE21"] == "delimited"


def test_both_spellings_of_a_month_are_tried(tmp_path):
    # January to September are published as 2021-1.zip; October onwards as 2021-11.zip. Asking
    # for the wrong one is a 404 that reads like "this month was never published".
    assert [url.rsplit("/", 1)[-1] for url in verify_archive.archive_candidates("2021-1")] == [
        "2021-1.zip",
        "2021-01.zip",
    ]
    assert [url.rsplit("/", 1)[-1] for url in verify_archive.archive_candidates("2021-11")] == [
        "2021-11.zip"
    ]


def test_the_evidence_file_says_which_bytes_were_read(tmp_path):
    archive = build_archive(tmp_path / "2021-1.zip", [row(1, "701-1-SE21")])
    finding = {"organismo": "SERVICIO DE PRUEBA", "period": "2021-1", "total_clp": 3}

    coverage = verify_archive.verify("2021-1", ["701-1-SE21"], archive=archive)
    evidence = verify_archive.evidence_of(coverage, finding)

    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert evidence["archive_sha256"] == expected
    assert evidence["archive_bytes"] == archive.stat().st_size
    assert evidence["member"] == "2021-1.csv"
    assert evidence["finding"]["organismo"] == "SERVICIO DE PRUEBA"
    assert evidence["coverage"] == 1.0
    # A citation to a mutable blob is not a citation unless it says when it was read.
    assert evidence["retrieved_at"]
    json.dumps(evidence)  # and it is serialisable, because dossier_real.py reads it


def test_a_reviewer_can_check_every_cited_line_with_csv_and_a_number(tmp_path):
    """The end-to-end promise: line N of the named member holds the named code. Nothing else."""
    rows = [row(index, f"701-{index}-SE21") for index in range(1, 11)]
    archive = build_archive(tmp_path / "2021-1.zip", rows)
    codes = ["701-3-SE21", "701-7-SE21", "701-10-SE21"]

    coverage = verify_archive.verify("2021-1", codes, archive=archive)
    with zipfile.ZipFile(archive) as bundle:
        physical = bundle.read(coverage.member).decode("utf-8").splitlines()

    for code in codes:
        line = physical[coverage.lines[code] - 1]
        fields = next(csv.reader([line], delimiter=";"))
        assert code in fields


def test_a_cached_archive_keeps_the_url_that_actually_answered(tmp_path, monkeypatch):
    # Rebuilding the URL from the month on a cache hit cited 2021-01.zip for an archive that only
    # exists as 2021-1.zip: a dead link in every dossier built offline. The URL is recorded.
    cache = tmp_path / "cache"
    cache.mkdir()
    build_archive(cache / "2021-1.zip", [row(1, "701-1-SE21")])
    (cache / "2021-1.url").write_text("https://example.invalid/oc-da/2021-1.zip", encoding="utf-8")

    def explode(*args, **kwargs):  # a cache hit must not touch the network
        raise AssertionError("download() tried to fetch a cached archive")

    monkeypatch.setattr(verify_archive.urllib.request, "urlopen", explode)
    path, cached, url = verify_archive.download("2021-1", cache)

    assert cached is True
    assert path.name == "2021-1.zip"
    assert url == "https://example.invalid/oc-da/2021-1.zip"


def test_missing_codes_are_reported_not_raised_unless_asked(tmp_path):
    archive = build_archive(tmp_path / "2021-1.zip", [row(1, "701-1-SE21")])
    out = tmp_path / "evidence.json"

    exit_code = verify_archive.main(
        [
            "verify_archive.py",
            "--month",
            "2021-1",
            "--codes",
            "701-1-SE21",
            "701-404-SE21",
            "--archive",
            str(archive),
            "--out",
            str(out),
            "--quiet",
        ]
    )
    assert exit_code == 0
    assert json.loads(out.read_text())["missing"] == ["701-404-SE21"]

    strict = verify_archive.main(
        [
            "verify_archive.py",
            "--month",
            "2021-1",
            "--codes",
            "701-404-SE21",
            "--archive",
            str(archive),
            "--out",
            str(out),
            "--require-all",
            "--quiet",
        ]
    )
    assert strict == 2


def test_a_signal_line_supplies_its_own_codes_and_month(tmp_path):
    signals = tmp_path / "signals.jsonl"
    signals.write_text(
        json.dumps(
            {
                "organismo": "SERVICIO DE PRUEBA",
                "proveedor": "PROVEEDOR UNO",
                "period": "2021-4",
                "order_codes": ["1467-17-SE21", "1467-18-SE21"],
                "total_clp": 300_000_000,
                "ceiling_clp": 51_000_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    codes, finding, period = verify_archive.codes_from_signal(signals, 0)

    assert codes == ["1467-17-SE21", "1467-18-SE21"]
    assert period == "2021-4"
    assert finding["factor"] == pytest.approx(5.882, abs=1e-3)
