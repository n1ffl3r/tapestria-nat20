.PHONY: check lint format format-check type test coverage security smoke test-roundtrip test-hashseed
check: lint format-check type coverage security test-hashseed
lint:
	uv run ruff check .
format:
	uv run ruff format .
format-check:
	uv run ruff format --check .
type:
	uv run mypy src/
test:
	uv run pytest -q
# Baseline-and-ratchet coverage gate (75% observed today; floor set 2pts under).
coverage:
	uv run pytest -q --cov=dnd5e_engine --cov-report=term-missing --cov-fail-under=90
security:
	uv run bandit -r src/ -c pyproject.toml -q
smoke:
	bash scripts/smoke_clean_install.sh
# Spec 02a: the whole suite again, every handle call first rebuilding its
# combat from its own CombatState through JSON (tests/conftest.py). A failure
# here and not under `test` names state CombatState drops.
test-roundtrip:
	NAT20_ROUNDTRIP_STATE=1 uv run pytest -q
# Spec 02a, in `check`: the round trip twice, under two string-hash seeds, tracing every
# state; the traces must match byte for byte, or some output follows set order.
TRACE_DIR := $(shell mktemp -d -u)
test-hashseed:
	NAT20_ROUNDTRIP_STATE=1 NAT20_STATE_TRACE=$(TRACE_DIR)/1 PYTHONHASHSEED=1 uv run pytest -q
	NAT20_ROUNDTRIP_STATE=1 NAT20_STATE_TRACE=$(TRACE_DIR)/2 PYTHONHASHSEED=2 uv run pytest -q
	diff -r $(TRACE_DIR)/1 $(TRACE_DIR)/2
	rm -rf $(TRACE_DIR)
