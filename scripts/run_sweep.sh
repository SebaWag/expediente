#!/bin/bash
# Sweep de extraction: reanudable, sin buffer, y desacoplado de la terminal.
#
# El 30-oct este script se lanzó tres veces y no dejó un solo resultado. Las tres
# causas estaban acá, no en el modelo:
#
#   1. `> out/sweep.log` truncaba el log de la corrida anterior en cada relanzamiento,
#      así que la evidencia de por qué murió la primera se borraba al reintentar.
#   2. `python3` escribe a bloque cuando el stdout es un archivo: al morir por señal,
#      el buffer (incluido el "examples: 800") se iba con el proceso. De ahí `-u`.
#   3. El proceso colgaba de la terminal que lo lanzó. Si la sesión cerraba, moría.
#      De ahí `setsid nohup` en lanzar_sweep.sh.
#
# Uso:  bash scripts/run_sweep.sh low          (o "low,medium,high")
#       bash scripts/run_sweep.sh low --relanzar
cd "$(dirname "$0")/.." || exit 1

EFFORTS="${1:-low}"
WORKERS="${WORKERS:-8}"
EXTRA=""
if [ "${2:-}" != "--relanzar" ]; then
  EXTRA="--resume"
fi

echo "$$" > out/sweep.pid
echo "=== START job=$EFFORTS workers=$WORKERS resume=${EXTRA:-no} $(date -Is) pid=$$ ==="

for e in ${EFFORTS//,/ }; do
  echo "=== START $e $(date -Is) ==="
  python3 -u benchmarks/extract_bench.py \
    --examples benchmarks/extract_gold.jsonl \
    --efforts "$e" \
    --workers "$WORKERS" \
    --journal "out/journal_${e}.jsonl" \
    --json-out "out/sweep_${e}.json" \
    --progress-every 25 \
    $EXTRA 2>&1
  rc=$?
  echo "=== END $e $(date -Is) rc=$rc ==="
  # Un effort que falla no debe impedir el siguiente: se anota y se sigue.
done

echo "=== END job=$EFFORTS $(date -Is) ==="
