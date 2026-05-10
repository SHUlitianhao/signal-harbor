.PHONY: check test compile ingest ingest-public run

PYTHON ?= python3
export PYTHONPATH := backend

check:
	$(PYTHON) scripts/check.py

test:
	$(PYTHON) -m unittest discover -s tests

compile:
	$(PYTHON) -m compileall -q backend scripts

ingest:
	$(PYTHON) scripts/ingest_fixture.py

ingest-public:
	$(PYTHON) scripts/ingest_public_sources.py

run:
	$(PYTHON) scripts/run_dev.py
