# OCDS findings — the supplier registry the bulk does not have

**Date:** 2026-10-28 · **Sample:** 1.200 awards from 3 months (2025-09, 2025-11, 2026-01)

## Why this document exists

The deterministic entity resolver (`expediente/resolution/resolver.py`) asserts two
relations from exact matches: **shared legal representative** and **shared address**.
Neither field exists in the ChileCompra bulk archives, which carry only
`proveedor`, `proveedor_rut`, `proveedor_tamano`, `region_proveedor` and `rubro`.

Consequence: on the bulk dataset, the only signal that actually runs is the
`name_key` blocking step, and because ChileCompra canonicalises one name per RUT,
that step finds almost nothing (see §4). The "supplier network" detector was
designed against data we did not have.

This document records the source that does have it, and what it measured.

## 1 · The source

`https://api.mercadopublico.cl/APISOCDS/OCDS/` — **open, no ticket**, standard OCDS.

```
GET /listaOCDSAgnoMes/{year}/{month}/{offset}/{limit}   → ocids + per-doc urls
GET /award/{codigo}                                     → awards + parties
GET /tender/{codigo}                                    → tender + buyer party
```

The `parties` array carries suppliers with `roles: ["supplier","tenderer"]` and:

```json
{
  "identifier": { "id": "76939480K", "legalName": "JOSE LUIS PAVEZ SPA", "scheme": "CL-RUT" },
  "address":    { "streetAddress": "PEDRO LAGOS N° 187", "region": "Región del Biobío" },
  "contactPoint": { "name": "CAROLINA BAEZA VILLA" },
  "name": "S.G.C DIESEL LTDA | Jose Luis Pavez SpA - BOSCH Car Service"
}
```

### What does NOT work

`GET /servicios/v1/publico/Empresas.json` returns **HTTP 500** for every parameter
combination tried (`?rut=`, no params, with/without dots in the RUT). The endpoint is
broken server-side; the ticket is not the problem (it is valid and 36 chars). Do not
route supplier lookups through the v1 API.

## 2 · Coverage (measured, n = 863 unique suppliers)

| Field | Coverage |
|---|---|
| `identifier.id` (RUT) | 863 / 863 · 100% |
| `address.streetAddress` | 862 / 863 · **99,9%** |
| `address.region` | 860 / 863 · **99,7%** |
| `contactPoint.name` | 863 / 863 · **100%** |

**Caveat that must be reported, not hidden:** only **671 of 1.200 awards (55,9%)**
expose a supplier party at all. The other 44% carry no award data or no parties.
Any recall figure on the network detector is bounded by that coverage.

## 3 · Detector #3 is viable

Address blocking over the 863 suppliers, normalised with the same rules as
`domain/normalizers.py`:

| Metric | Value |
|---|---|
| Distinct normalised addresses | 847 |
| Addresses shared by ≥ 2 suppliers | **14** |
| Candidate pairs | **16** |
| Suppliers involved | 29 · **3,4%** |

Worked example (genuine candidate — same address, near-identical name, different RUT):

```
Valle de Casablanca Norte 8219 · RM
  772901941  Cardiotec Vascularis SpA
  762641380  CARDIOTEC VASCULAR LIMITADA
```

Counter-example, and the reason these stay **candidates** and never become relations:

```
CARLOS FERNANDEZ N° 244 · RM
  77478120K  FRESENIUS KABI CHILE LIMITADA
  915460003  LABORATORIO SANDERSON SPA
```

Two unrelated companies in one office building. `resolver.py` already documents the
rule ("*a naive system merges those two companies and creates a false accusation*");
the real data confirms it. Shared address is **evidence to review**, not identity.

## 4 · The labelled set fell out of this

The bulk cannot produce hard name pairs, because ChileCompra canonicalises one
canonical name per RUT: across all 4.950.443 orders there are only 166 RUTs with
more than one spelling, and those differ only in case and accents.

OCDS is different. The `name` field concatenates trade names separated by `|`, and
**539 of 863 suppliers (62,5%) carry two genuinely different names** (case-insensitive,
punctuation-stripped comparison — not an artefact of capitalisation):

```
92288000K  legal=DROGUERIA HOFMANN LIMITADA Y COMPANIA SOCIEDAD EN COMANDITA POR ACCION
             trade=DROGUERIA HOFMANN SAC
             trade=Drogueria Hofmann

762997584  legal=LABORATORIO FLEX PHARMA CHILE SPA
             trade=LABORATORIO FLEX PHARMA CHILE LIMITADA
             trade=Flex Pharma

769869247  legal=MDC HEALTH SPA
             trade=MDC HEALTH
```

Each of these is a **positive pair with free ground truth**: same RUT means same legal
entity, no human judgement required. That yields **539 labelled positive pairs** from
a single afternoon of sampling — and the pair difficulty is real (`Flex Pharma` vs
`LABORATORIO FLEX PHARMA CHILE SPA`), not `UNIVERSIDAD DE CONCEPCION` vs
`Universidad de Concepción`.

This is the substrate for the entity-resolution benchmark: positives from the `|`
split, negatives from address-blocked groups whose names and RUTs disagree, and a
human-audited sample to attach an error bar.

## 5 · Artefacts

| File | Contents |
|---|---|
| `benchmarks/raw_ocds_awards.jsonl` | 1.200 raw award payloads (0 errors after retry) |
| `benchmarks/ocds_suppliers_2000.json` | 863 suppliers with name/address/contact |
| `benchmarks/shared_address_groups.json` | 14 address groups, 16 pairs |
| `benchmarks/real_name_variants.json` | 539 suppliers with 2 distinct names |

## 6 · Method notes (so the numbers can be trusted)

- Concurrency of 10 workers produced **490/1.200 HTTP 429** responses. Retrying those
  490 serially with exponential backoff recovered **490/490**. The first pass was
  biased toward fast responses; the figures above come from the repaired set.
- An earlier revision of this analysis reported "100% of suppliers have name
  variants". That was wrong: the `name` field often repeats `legalName` twice with
  different capitalisation. The 62,5% figure above only counts pairs that differ
  after case-folding and punctuation stripping.
- Costs nothing: the OCDS API requires no ticket and returned no rate limiting at
  ~2 requests/second against a single month listing.

## 7 · What this changes

1. **Detector #3 stops being a design fiction.** It has a data source, and the
   address field is effectively complete for suppliers who appear in OCDS.
2. **The graph can carry `:Domicilio` nodes with real evidence**, which is what the
   navigation agent needs to demonstrate a multi-hop path that a spreadsheet cannot.
3. **The entity-resolution benchmark is now data-driven**, with 539 free positive
   pairs instead of 9 hand-written examples.
4. **A recall ceiling must be stated**: 44% of awards publish no supplier party, so
   any "we found N of M" claim is bounded by that number.
