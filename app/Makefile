.PHONY: install test run-world run-broker run-game run-console

install:
	uv sync

test:
	uv run pytest

run-world:
	uv run uvicorn world.main:app --reload --port 8100

run-broker:
	uv run uvicorn broker.app:app --reload --port 8200

run-game:
	uv run uvicorn game.server:app --reload --port 8400

run-console:
	uv run uvicorn game.console.app:app --reload --port 8300
