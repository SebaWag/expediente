# Verifying a citation, and the four things that were wrong

A dossier's claim is a sentence with a footnote, and the footnote points at a line of an
official file. On 30-oct I verified one chain **by hand**: downloaded `oc-da/2021-11.zip`,
searched nine order codes in a 653 MB CSV, and wrote the nine line numbers into a JSON file.

That produced a true citation and a useless method. A reviewer has no reason to trust a line
number I typed, and I have no way to produce the second one without repeating an hour of work.
`scripts/verify_archive.py` is that hour turned into code, and `scripts/dossier_pipeline.py`
is the loop around it: **signal → archive → verified lines → dossier**, in one command.

```
python3 scripts/dossier_pipeline.py --pick 3 --distinct-months --min-factor 3 --outdir out/dossiers
```

Measured, 2026-10-31:

| | |
|---|---|
| Signals read | 1.106 chains (detector 1, five years) |
| Chains selected | 3, one per month, factor ≥ 3x, ≤ 12 orders |
| Corpus read | **10.302.746 orders** (11.3 GB), one pass |
| Archives downloaded | 3 (`2021-1.zip`, `2021-2.zip`, `2021-4.zip`) |
| Codes located | **16 / 16 (100 %)** |
| Dossiers emitted | **3 / 3**, all `signable`, citation rate 100 % |
| Wall time | **39 s** |

And the check that matters: re-opening the three archives and reading line `N` of the named
member independently reproduces **16/16** citations. The nine lines verified by hand on
2021-11 come out identical — `36451`, `765954`, `768089`, `768378`, `787916`, `867937`,
`868360`, `885309`, `886128`.

## The three properties the code promises

1. **It never guesses.** A code that is not in the archive gets no line. It is reported as
   missing, with a coverage figure the dossier inherits, and the evidence gate downstream
   decides whether a claim survives without it. `--require-all` turns a partial verification
   into a non-zero exit, for when a partial answer is not an answer.
2. **The line number means what `grep -n` means.** Lines are counted over the physical bytes of
   the CSV, from 1, so `sed -n '36451p'` shows the record. A "page" a reviewer cannot check is
   not a citation.
3. **The reading is recorded.** `sha256`, byte size, member name, retrieval timestamp and the
   URL that answered travel with the evidence. A citation to a mutable blob is not a citation
   unless it says which bytes were read.

## The four defects, in the order they bit

None of these were caught by reading the code. Each one produced a plausible output.

### 1 · The delimiter is not a comma, and 0/9 looks like "the data isn't there"

The archive ships `"ID";"Codigo";...` with semicolons. The first version split on `,`, so every
line parsed as a single field and **nine real codes were reported missing**. The failure mode is
the dangerous one: a verifier that finds nothing reads as a verifier that checked, and the
honest conclusion ("these orders are not in the official file") would have been false.
Now the delimiter is sniffed from the header, and `test_delimiter_is_sniffed_from_the_header`
holds it.

### 2 · A buyer is a RUT, not a name — and the chain was cut in half in silence

The first pipeline selected orders by `organismo` **name** and then filtered by one RUT, because
`load_buyer_orders` takes a RUT. "SERVICIO NACIONAL DE PESCA" is **47 buyer units** in the
corpus; the slice collected 80 orders, the detector saw 18, and the chain that the signal
reported at 3,3x came back as **0 findings**. It looked exactly like a detector bug.

The detector groups by `(organismo_rut, proveedor, mes)`. So the slice is keyed by RUT, and the
RUT of a chain is the slice that holds **most of its codes** — not the first one, because a
majority is evidence and a first element is a guess. This is the defect I would have shipped:
the dossier was valid, well-formed, signable, and empty.

### 3 · January is `2021-1.zip`; November is `2021-11.zip`

The publisher is inconsistent: months 1–9 unpadded, 10–12 padded. Asking for `2021-01.zip`
returns 404, which reads as *"that month was never published"* — a data-availability conclusion
drawn from a filename convention. Both spellings are tried, and the one that answered is the one
recorded.

### 4 · A cache hit cited a URL that 404s

With the archive cached, the code rebuilt the URL from the month instead of remembering the one
that answered, so three dossiers cited `2021-01.zip` while the file only exists as `2021-1.zip`.
Every footnote would have been dead in exactly the case that matters — building the dossier
offline, from evidence already gathered. The URL is now written next to the archive
(`2021-1.url`) and an unrecorded archive is probed with HEAD rather than guessed.

## What this does not do

- **It verifies the monthly `oc-da` archives only.** No PDFs, no OCR, no Contraloría reports.
  A claim about a page of a scanned resolution is a different pipeline, and the honest state of
  that one is still zero.
- **Coverage below 100 % is reported, not repaired.** A chain whose orders are partly absent
  produces a dossier with fewer citations and a stated coverage; the missing codes are named.
- **It does not decide whether a finding is real.** The placebo control inside each dossier says
  what the finding may and may not be reported as; this pipeline only makes the citation
  checkable.
