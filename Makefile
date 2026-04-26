.PHONY: api api-bg api-status api-stop frontend install test frontend-build docker production-floor-check options-paper-readiness staging-env-check print-staging-boot staging-db-up staging-db-down use-local-staging-db staging-production-floor-check staging-api staging-api-bg staging-api-status staging-api-stop staging-options-paper-readiness strategy-validation strategy-snapshot-backfill strategy-snapshot-sample backend-market backend-identity backend-ops backend-execution backend-groups institutional-start institutional-stop institutional-status institutional-health institutional-kill institutional-reconcile institutional-replay institutional-test

BACKEND_PYTHON := backend\.venv\Scripts\python.exe
INSTITUTIONAL_PYTHON := $(if $(wildcard backend/.venv/Scripts/python.exe),$(BACKEND_PYTHON),python)
INSTITUTIONAL_CONFIG ?= institutional_trading/config/example.yaml

install:
	pip install -r backend/requirements.txt
	cd frontend && npm install

api:
	python -m backend.app

api-bg:
	backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py start --env-file .env

api-status:
	backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py status --env-file .env

api-stop:
	backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py stop --env-file .env

frontend:
	cd frontend && npm run dev

test:
	python -m unittest discover -s tests

frontend-build:
	cd frontend && npm run build

docker:
	docker compose up --build

production-floor-check:
	backend\.venv\Scripts\python.exe scripts/production_floor_check.py --probe-worker

options-paper-readiness:
	backend\.venv\Scripts\python.exe scripts/check_options_paper_readiness.py .env

staging-env-check:
	backend\.venv\Scripts\python.exe scripts/validate_staging_env.py .env.staging

print-staging-boot:
	backend\.venv\Scripts\python.exe scripts/print_staging_boot_command.py

staging-db-up:
	docker compose -f docker-compose.staging.yml up -d postgres

staging-db-down:
	docker compose -f docker-compose.staging.yml down

use-local-staging-db:
	backend\.venv\Scripts\python.exe scripts/use_local_staging_postgres.py .env.staging

staging-production-floor-check:
	backend\.venv\Scripts\python.exe scripts/run_with_env.py .env.staging -- backend\.venv\Scripts\python.exe scripts\production_floor_check.py --probe-worker

staging-api:
	backend\.venv\Scripts\python.exe scripts/run_with_env.py .env.staging -- backend\.venv\Scripts\python.exe -m backend.app

staging-api-bg:
	backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py start --env-file .env.staging

staging-api-status:
	backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py status --env-file .env.staging

staging-api-stop:
	backend\.venv\Scripts\python.exe scripts/manage_api_runtime.py stop --env-file .env.staging

staging-options-paper-readiness:
	backend\.venv\Scripts\python.exe scripts/check_options_paper_readiness.py .env.staging

strategy-validation:
	backend\.venv\Scripts\python.exe scripts/run_strategy_validation.py --tenant-slug alpha-desk

strategy-snapshot-backfill:
	backend\.venv\Scripts\python.exe scripts/backfill_equity_snapshot.py --tenant-slug alpha-desk

strategy-snapshot-sample:
	backend\.venv\Scripts\python.exe scripts/sample_equity_snapshots.py --tenant-slug alpha-desk --count 5 --sleep-seconds 2

backend-market:
	backend\.venv\Scripts\python.exe scripts/run_backend_test_groups.py market-desk

backend-identity:
	backend\.venv\Scripts\python.exe scripts/run_backend_test_groups.py identity-platform

backend-ops:
	backend\.venv\Scripts\python.exe scripts/run_backend_test_groups.py ops-readiness

backend-execution:
	backend\.venv\Scripts\python.exe scripts/run_backend_test_groups.py execution-trade

backend-groups:
	backend\.venv\Scripts\python.exe scripts/run_backend_test_groups.py list

institutional-start:
	$(INSTITUTIONAL_PYTHON) scripts/manage_institutional_trading.py --config $(INSTITUTIONAL_CONFIG) start

institutional-stop:
	$(INSTITUTIONAL_PYTHON) scripts/manage_institutional_trading.py --config $(INSTITUTIONAL_CONFIG) stop

institutional-status:
	$(INSTITUTIONAL_PYTHON) scripts/manage_institutional_trading.py --config $(INSTITUTIONAL_CONFIG) status

institutional-health:
	$(INSTITUTIONAL_PYTHON) scripts/manage_institutional_trading.py --config $(INSTITUTIONAL_CONFIG) health

institutional-kill:
	$(INSTITUTIONAL_PYTHON) scripts/manage_institutional_trading.py --config $(INSTITUTIONAL_CONFIG) kill --reason operator_requested

institutional-reconcile:
	$(INSTITUTIONAL_PYTHON) scripts/manage_institutional_trading.py --config $(INSTITUTIONAL_CONFIG) reconcile

institutional-replay:
	$(INSTITUTIONAL_PYTHON) scripts/manage_institutional_trading.py --config $(INSTITUTIONAL_CONFIG) replay

institutional-test:
	$(INSTITUTIONAL_PYTHON) -m unittest discover -s institutional_trading/tests -t .
