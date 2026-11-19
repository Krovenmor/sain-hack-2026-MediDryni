.DEFAULT_GOAL := help

PLANTUML ?= plantuml
export PATH := $(abspath .venv/bin):$(PATH)

.PHONY: help docs run test

help:
	@printf '%s\n' \
		'make docs  Generate docs/plan/c2-component.svg (requires PlantUML)' \
		'make run   Start the server (requires HACK_API_URL, PORT defaults to 8000)' \
		'make test  Run the unittest suite'

docs:
	DISPLAY= $(PLANTUML) -nometadata -charset UTF-8 -tsvg docs/plan/c2-component.puml

run:
	sh start.sh

test:
	PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
