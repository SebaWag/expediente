#!/bin/bash
cd /home/seba/expediente
for e in low medium high; do
  echo "=== START $e $(date -Is) ==="
  python3 benchmarks/extract_bench.py \
    --examples benchmarks/extract_gold.jsonl \
    --efforts "$e" --workers 8 \
    --json-out "out/sweep_${e}.json" 2>&1
  echo "=== END $e $(date -Is) rc=$? ==="
done
