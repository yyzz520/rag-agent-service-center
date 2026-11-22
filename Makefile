PYTHON ?= .venv/bin/python

.PHONY: venv install install-dev test lint verify eval-gate export-finetune seed seed-eval run

.venv:
	python3 -m venv .venv

venv: .venv

install: .venv
	$(PYTHON) -m pip install -r requirements.txt

install-dev: .venv
	$(PYTHON) -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check app tests

verify: lint test

eval-gate:
	$(PYTHON) scripts/eval_gate.py

export-finetune:
	$(PYTHON) scripts/export_finetune.py

seed:
	$(PYTHON) scripts/seed_demo.py

seed-eval:
	$(PYTHON) scripts/seed_demo.py --eval

run:
	DB_URL='sqlite:///data/app.db' \
	CHECKPOINT_BACKEND=sqlite \
	REDIS_URL='' \
	CHROMA_HOST='' \
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8200
