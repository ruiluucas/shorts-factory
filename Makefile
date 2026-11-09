# Shorts Factory — alvos de desenvolvimento
# Uso: make setup | make test | make lint | make eval | make run-demo

SHELL := /bin/bash
PY := .venv/bin/python
PYTEST := $(PY) -m pytest
RUFF := .venv/bin/ruff
DEMO_VIDEO := tests/fixtures/fala_reativa.mp4
DEMO_JOB := demo

.DEFAULT_GOAL := help
.PHONY: help setup test lint lint-fix eval run-demo fixtures clean

help:  ## Mostra os alvos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup:  ## Cria venv 3.12, instala deps Python e Node
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements.txt
	uv pip install --python $(PY) ruff pytest
	cd remotion && npm install
	@echo "✓ setup completo"

test:  ## Roda a suíte de testes
	$(PYTEST) tests/ -q

lint:  ## Checa estilo e imports (não modifica nada)
	$(RUFF) check .
	$(RUFF) format --check .

lint-fix:  ## Corrige o que for seguro automaticamente
	$(RUFF) check . --fix
	$(RUFF) format .

fixtures:  ## Gera os vídeos sintéticos de teste (ffmpeg)
	$(PY) tests/fixtures/make_fixtures.py

eval:  ## (planejado) harness de avaliação — ainda não implementado
	@if [ -f eval/run_eval.py ]; then \
		$(PY) eval/run_eval.py; \
	else \
		echo "eval/run_eval.py ainda não existe — o harness de avaliação está"; \
		echo "planejado mas não implementado. Rode 'make test' para a suíte atual."; \
		exit 1; \
	fi

run-demo:  ## Pipeline completo no vídeo de demonstração
	@test -f $(DEMO_VIDEO) || (echo "vídeo ausente: $(DEMO_VIDEO) — rode 'make fixtures'" && exit 1)
	$(PY) main.py init-job --job-id $(DEMO_JOB) --video $(DEMO_VIDEO) \
		--creator "@demo" --evidence /tmp/demo.png --approved
	$(PY) main.py run --video $(DEMO_VIDEO) --job-id $(DEMO_JOB)

clean:  ## Remove caches (mantém o venv)
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
	@echo "✓ caches limpos"
