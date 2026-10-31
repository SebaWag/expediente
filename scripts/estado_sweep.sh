#!/bin/bash
# Estado del sweep de extraction en un vistazo. Uso: ./scripts/estado_sweep.sh
cd "$(dirname "$0")/.."
echo "=== $(date -Is) ==="
for e in low medium high; do
  if [ -f "out/sweep_${e}.json" ]; then
    python3 - "$e" <<'PY'
import json, sys
e = sys.argv[1]
d = json.load(open(f"out/sweep_{e}.json"))
for name, r in d.get("runs", {}).items():
    print(f"  ✅ {e:7} exacto={r['exacto']:.3f}  no_verif={r['no_verificado']}/{r['campos_evaluados']}  causas={r.get('causas')}")
PY
  else
    echo "  ⏳ $e: aún corriendo o pendiente"
  fi
done
echo
if pgrep -f "extract_bench" >/dev/null; then
  P=$(pgrep -f "extract_bench" | head -1)
  echo "  proceso vivo · etime=$(ps -o etime= -p $P | tr -d ' ') · conexiones=$(ss -tnp 2>/dev/null | grep -c "$P")"
else
  echo "  sin proceso extract_bench activo"
fi
echo
tail -3 out/sweep.log
