#!/usr/bin/env python3
"""Build the final entity-resolution benchmark: hard positives and address negatives.

The first benchmark (1.249 pairs, `docs/ENTITY_BENCHMARK.md`) answered whether a
threshold can replace a human. Its honest limitation was written down: *"positives are
easier than the real distribution — many are the same string with different
capitalisation or legal form."* A recall curve computed on those positives is
optimistic by construction, and the document said so.

This script closes that gap with two strata the first set did not have:

    hard_positive      same RUT, names that differ after normalisation —
                       "DROGUERIA HOFMANN LIMITADA Y CIA..." vs "Drogueria Hofmann".
                       The truth still comes from the RUT, not from a model.

    address_negative   different RUTs, the same `streetAddress` — the Fresenius and
                       Sanderson case: two companies in one office building. These are
                       the negatives a blocking strategy creates, and they are the ones
                       that cost a false accusation if the threshold is wrong.

The adversarial negatives and easy negatives from the first run are carried over
unchanged, so the four strata are comparable and the old numbers remain the old
numbers.

Scoring:
    cosine   dot product of the cached qwen3-embedding-8b vectors (no API call)
    rerank   qwen3-reranker-8b via Fireworks, one call per pair, retried with backoff

Usage:
    FIREWORKS_API_KEY=... python3 benchmarks/build_entity_benchmark.py --workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SEED = 20261029


def normalise(text: str) -> str:
    """Fold accents, drop punctuation, collapse whitespace. Comparison only."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isalnum() else " " for c in folded).split()).upper()


def load_index() -> tuple[dict[str, int], np.ndarray]:
    rows = json.loads((HERE / "name_index.json").read_text(encoding="utf-8"))
    matrix = np.load(HERE / "name_emb.npy")
    if len(rows) != matrix.shape[0]:
        raise SystemExit(f"index and embeddings disagree: {len(rows)} vs {matrix.shape[0]}")
    lookup: dict[str, int] = {}
    for position, row in enumerate(rows):
        lookup.setdefault(normalise(row["text"]), position)
    # Unit vectors once, so every cosine is a dot product.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return lookup, matrix / np.clip(norms, 1e-12, None)


def cosine_of(lookup: dict[str, int], matrix: np.ndarray, left: str, right: str) -> float | None:
    a, b = lookup.get(normalise(left)), lookup.get(normalise(right))
    if a is None or b is None:
        return None
    return float(np.dot(matrix[a], matrix[b]))


def hard_positives() -> list[dict]:
    """Same RUT, genuinely different names. The stratum the first run lacked."""
    variants = json.loads((HERE / "real_name_variants.json").read_text(encoding="utf-8"))
    pairs: list[dict] = []
    for supplier in variants:
        legal = supplier["legalName"]
        for variant in supplier["variants"]:
            # A variant that is the legal name with different case or punctuation is
            # not a hard test; the first benchmark already had 952 of those.
            if normalise(legal) == normalise(variant):
                continue
            pairs.append(
                {
                    "rut_a": supplier["rut"],
                    "name_a": legal,
                    "rut_b": supplier["rut"],
                    "name_b": variant,
                    "stratum": "hard_positive",
                    "truth": "same",
                }
            )
    pairs.sort(key=lambda p: (p["rut_a"], p["name_b"]))
    return pairs


def address_negatives() -> list[dict]:
    """Different RUTs at the same address. A shared building is not an identity."""
    groups = json.loads((HERE / "shared_address_groups.json").read_text(encoding="utf-8"))
    pairs: list[dict] = []
    for address, members in sorted(groups.items()):
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                left, right = members[i], members[j]
                if left[0] == right[0]:
                    continue  # same RUT is not a negative
                pairs.append(
                    {
                        "rut_a": left[0],
                        "name_a": left[1],
                        "rut_b": right[0],
                        "name_b": right[1],
                        "stratum": "address_negative",
                        "truth": "different",
                        "address": address,
                    }
                )
    pairs.sort(key=lambda p: (p["address"], p["rut_a"], p["rut_b"]))
    return pairs


def carry_over() -> list[dict]:
    """The first benchmark's strata, unchanged, so old and new are comparable."""
    rows = json.loads((HERE / "pairs_reranked.json").read_text(encoding="utf-8"))
    out = []
    for row in rows:
        kept = {
            "rut_a": row["rut_a"],
            "name_a": row["name_a"],
            "rut_b": row["rut_b"],
            "name_b": row["name_b"],
            "stratum": row["stratum"],
            "truth": row["truth"],
            "cosine": row.get("cosine"),
            "rerank": row.get("rerank"),
            "origin": "run_1",
        }
        out.append(kept)
    return out


def rerank_one(session: dict, query: str, document: str, attempts: int = 5) -> float | None:
    """One reranker call, with backoff. Returns None if it never answered."""
    payload = json.dumps(
        {"model": session["model"], "query": query, "documents": [document], "top_n": 1}
    ).encode()
    for attempt in range(attempts):
        request = urllib.request.Request(session["url"], data=payload, method="POST")
        request.add_header("Authorization", f"Bearer {session['key']}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = json.loads(response.read())
            return float(body["data"][0]["relevance_score"])
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2**attempt)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default=str(HERE / "pairs_final.json"))
    args = parser.parse_args()

    lookup, matrix = load_index()

    new_pairs = hard_positives() + address_negatives()
    old_pairs = carry_over()
    pairs = new_pairs + old_pairs

    print(f"hard positives:     {len(hard_positives()):>4}")
    print(f"address negatives:  {len(address_negatives()):>4}")
    print(f"carried over:       {len(old_pairs):>4}  (run 1)")
    print(f"total:              {len(pairs):>4}\n")

    # Cosine for everything; the old rows keep their measured value.
    for pair in new_pairs:
        pair["cosine"] = cosine_of(lookup, matrix, pair["name_a"], pair["name_b"])
        pair["rerank"] = None
        pair["origin"] = "run_2"

    missing = [p for p in new_pairs if p["cosine"] is None]
    if missing:
        print(f"warning: {len(missing)} pairs are not in the embedding index", file=sys.stderr)

    # Rerank only what has no score yet: the old rows are not paid for twice.
    pending = [p for p in new_pairs if p.get("rerank") is None]
    key = os.environ.get("FIREWORKS_API_KEY", "").strip()
    if not key:
        print("FIREWORKS_API_KEY is not set: rerank will be null", file=sys.stderr)
    elif pending:
        session = {
            "key": key,
            "model": os.environ["FIREWORKS_RERANK_MODEL"],
            "url": os.environ.get("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1").rstrip("/")
            + "/rerank",
        }
        print(f"reranking {len(pending)} pairs with {args.workers} workers...")
        done = 0
        started = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(rerank_one, session, pair["name_a"], pair["name_b"]): pair
                for pair in pending
            }
            for future in as_completed(futures):
                futures[future]["rerank"] = future.result()
                done += 1
                if done % 100 == 0:
                    elapsed = time.time() - started
                    print(f"  {done}/{len(pending)} · {elapsed:.0f}s · {done / max(elapsed, 1e-9):.1f}/s")

    succeeded = sum(1 for p in pairs if p.get("rerank") is not None)
    print(f"\nrerank scores: {succeeded}/{len(pairs)}")

    out = Path(args.out)
    out.write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
