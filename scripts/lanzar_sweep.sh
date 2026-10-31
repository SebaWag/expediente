#!/bin/bash
# Lanza el sweep desacoplado de la terminal, con log que se acumula en vez de truncarse.
#
# La regla es simple: la corrida no puede depender de que esta terminal siga abierta
# ni de que nadie recuerde volver a mirarla. `setsid` la pone en su propia sesión, así
# que cerrar la sesión SSH no la mata, y el log se abre en modo append (`>>`) para que
# el relanzamiento no borre el diagnóstico de la corrida anterior.
#
# Uso:  bash scripts/lanzar_sweep.sh low
#       bash scripts/lanzar_sweep.sh low,medium,high
cd "$(dirname "$0")/.." || exit 1

EFFORTS="${1:-low}"
LOG="out/sweep.log"
mkdir -p out

if pgrep -f "extract_bench.py" >/dev/null; then
  echo "Ya hay un sweep corriendo (pid $(pgrep -f 'extract_bench.py' | head -1))."
  echo "Revisar: bash scripts/estado_sweep.sh"
  exit 1
fi

{
  echo
  echo "################ $(date -Is) · lanzando $EFFORTS ################"
} >> "$LOG"

setsid nohup bash scripts/run_sweep.sh "$EFFORTS" >> "$LOG" 2>&1 < /dev/null &
disown

sleep 2
echo "lanzado · efforts=$EFFORTS · log=$LOG"
echo "seguimiento: bash scripts/estado_sweep.sh   (o tail -f $LOG)"
