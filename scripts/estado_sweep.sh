#!/bin/bash
# Estado del sweep de extraction en un vistazo. Uso: ./scripts/estado_sweep.sh
#
# Lee el diario, no solo el JSON final. Un run que murió tiene diario y no tiene
# reporte: si este script solo mirara los .json diría "pendiente" para trabajo que
# sí se pagó, que es exactamente el error que nos costó tres corridas.
cd "$(dirname "$0")/.." || exit 1
echo "=== $(date -Is) ==="

for e in low medium high; do
  J="out/journal_${e}.jsonl"
  R="out/sweep_${e}.json"
  if [ -f "$J" ]; then
    N=$(wc -l < "$J")
    echo "  📓 $e: diario ${N}/800 documentos"
  else
    echo "  ·  $e: sin diario todavía"
  fi
  if [ -f "$R" ]; then
    python3 - "$e" <<'PY'
import json, sys
e = sys.argv[1]
d = json.load(open(f"out/sweep_{e}.json"))
for name, r in d.get("runs", {}).items():
    print(
        f"     ✅ {e:7} exacto={r['exacto']:.3f}  no_verif={r['no_verificado']}/{r['campos_evaluados']}"
        f"  truncados={r.get('truncados')}  causas={r.get('causas')}"
    )
PY
  fi
done

echo
if pgrep -f "extract_bench.py" >/dev/null; then
  P=$(pgrep -f "extract_bench.py" | head -1)
  echo "  proceso vivo pid=$P · etime=$(ps -o etime= -p "$P" | tr -d ' ')"
else
  echo "  sin proceso extract_bench activo"
fi
echo
echo "  --- últimas líneas del log ---"
tail -5 out/sweep.log 2>/dev/null || echo "  (sin log)"
