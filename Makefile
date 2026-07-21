.PHONY: install install-dev test test-cov lint format run docker-build clean

install:
	python -m pip install -r requirements.txt

install-dev:
	python -m pip install -r requirements-dev.txt
	pre-commit install

test:
	python -m pytest

test-cov:
	python -m pytest --cov=app --cov-report=term-missing --cov-report=xml

lint:
	ruff check app tests

format:
	ruff format app tests
	ruff check --fix app tests

run:
	python -m app.main

docker-build:
	docker build -t br-stremio-addon .

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov
