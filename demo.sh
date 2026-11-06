#!/usr/bin/env bash
# Demonstração end-to-end do pipeline GTA Shorts Factory.
# Cria um job, prova a trava jurídica e roda o pipeline completo.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PY=".venv/bin/python"
VIDEO="${1:-assets/source_videos/demo_source.mp4}"
JOB="${2:-demo_job}"

blue() { printf '\033[1;34m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red() { printf '\033[1;31m%s\033[0m\n' "$*"; }
gray() { printf '\033[0;90m%s\033[0m\n' "$*"; }

[ -f "$VIDEO" ] || { red "vídeo não encontrado: $VIDEO"; exit 1; }

echo
blue "════════════════════════════════════════════════"
blue " GTA SHORTS FACTORY — demonstração"
blue "════════════════════════════════════════════════"
gray "vídeo : $VIDEO"
gray "job   : $JOB"
echo

# ───────────────────────────── 1. cria o job (PENDING)
blue "[1] init-job — sem --approved (estado inicial seguro)"
$PY main.py init-job \
  --job-id "$JOB" \
  --video "$VIDEO" \
  --creator "@demo_canal" \
  --evidence "/tmp/permissao_demo.png" 2>&1 | tail -4
echo

# ───────────────────────────── 2. prova a trava
blue "[2] GATEKEEPER — tentativa de rodar com rights PENDING"
$PY main.py run --video "$VIDEO" --job-id "$JOB" --no-render >/tmp/gsf_gate.log 2>&1
CODE=$?
if [ "$CODE" -eq 3 ]; then
  green "✓ BLOQUEADO corretamente (exit 3 = REJECTED_RIGHTS)"
  gray "  $(grep -iE 'reject|bloque|rights' /tmp/gsf_gate.log | head -2 | tr '\n' ' ')"
else
  red "✗ FALHA DE SEGURANÇA: esperado exit 3, veio $CODE"
  tail -5 /tmp/gsf_gate.log
  exit 1
fi
echo

# ───────────────────────────── 3. aprova e roda
blue "[3] init-job --approved — declarando permissão explícita"
$PY main.py init-job \
  --job-id "$JOB" \
  --video "$VIDEO" \
  --creator "@demo_canal" \
  --evidence "/tmp/permissao_demo.png" \
  --approved 2>&1 | tail -3
echo

blue "[4] run — pipeline completo"
$PY main.py run --video "$VIDEO" --job-id "$JOB" --top-k 3 2>&1 | tail -30
CODE=$?
echo
if [ "$CODE" -eq 0 ]; then
  green "✓ pipeline concluído (exit 0)"
else
  red "⚠ pipeline terminou com exit $CODE"
fi
echo

# ───────────────────────────── 4. resultados
blue "[5] artefatos gerados"
ls -la "workspace/jobs/$JOB/" 2>/dev/null
echo

blue "[6] resultados"
$PY - <<'PYEOF'
import json, os
job = os.environ.get("JOB_DIR", "")
PYEOF

for f in candidates job_status; do
  p="workspace/jobs/$JOB/$f.json"
  if [ -f "$p" ]; then
    gray "── $f.json ──"
    head -c 600 "$p"; echo; echo
  fi
done
