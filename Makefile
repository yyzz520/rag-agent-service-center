PYTHON ?= .venv/bin/python
DAYS ?= 7

.PHONY: venv install install-dev test lint verify eval-gate export-finetune quality-report seed seed-eval reindex security run

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

quality-report:
	$(PYTHON) scripts/quality_report.py --days $(DAYS)

seed:
	$(PYTHON) scripts/seed_demo.py

seed-eval:
	$(PYTHON) scripts/seed_demo.py --eval

reindex:
	$(PYTHON) scripts/reindex_demo.py

security:
	$(PYTHON) scripts/check_secrets.py
	$(PYTHON) -m pip_audit -r requirements.txt || true

run:
	DB_URL='sqlite:///data/app.db' \
	CHECKPOINT_BACKEND=sqlite \
	REDIS_URL='' \
	CHROMA_HOST='' \
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8200
