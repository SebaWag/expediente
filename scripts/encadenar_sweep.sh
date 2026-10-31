#!/bin/bash
# Encadena el siguiente tramo del sweep cuando el actual termina.
#
# No es un detalle de comodidad: si la cadena depende de que alguien esté mirando,
# la cadena se rompe cuando ese alguien se va. El sweep anterior se perdió tres
# veces por depender de una terminal; este depende de que el proceso anterior haya
# salido, que es una condición que la máquina puede verificar sola.
#
# Uso:  bash scripts/encadenar_sweep.sh medium,high
cd "$(dirname "$0")/.." || exit 1

SIGUIENTE="${1:-medium,high}"

echo "=== $(date -Is) · esperando a que termine el sweep en curso ==="
while pgrep -f "extract_bench.py" >/dev/null; do
  sleep 20
done

echo "=== $(date -Is) · tramo anterior cerrado · encadenando $SIGUIENTE ==="
bash scripts/run_sweep.sh "$SIGUIENTE"
echo "=== $(date -Is) · cadena completa ==="
