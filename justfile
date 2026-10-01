# Justfile for mwphone

# Hard tabs, plz.
set indentation := "	"

# Lists & caching, plz.
set unstable
set lists

# We expect basic necessities to actually work...
set shell := ["bash", "-uc"]
set script-interpreter := ["bash", "-ue"]

# Project settings
# We currently prefer Python 3.14
PYTHON_VERSION := "3.14"

# Show available commands
list:
	@just --list

alias l := list
alias h := list
alias help := list
alias fmt := lint
alias b := build
alias c := clean
alias d := docs-serve
alias t := test
alias tc := type-check

# Type check the project with ty
[group('lint')]
type-check:
	uv run --python={{ PYTHON_VERSION }} ty check .

# Type check with concise output (one diagnostic per line)
[group('lint')]
type-check-concise:
	uv run --python={{ PYTHON_VERSION }} ty check --output-format=concise .

# Type check in watch mode (rechecks on file changes)
[group('lint')]
type-check-watch:
	uv run --python={{ PYTHON_VERSION }} ty check --watch .

# Formatting & linting passes
[group('lint')]
format:
	uv run --python={{ PYTHON_VERSION }} ruff format .
	uv run --python={{ PYTHON_VERSION }} ruff check . --fix
	uv run --python={{ PYTHON_VERSION }} ruff check --select I --fix .

# Linting of this very justfile
[group('lint')]
just-format:
	just --fmt

# Run all the formatting, linting and type-checking commands
[group('lint')]
lint: just-format format type-check-concise

# Run all the tests for all the supported Python versions
[group('test')]
testall:
	uv run --python={{ PYTHON_VERSION }} pytest .

# Run all the formatting, linting, and testing commands
[group('test')]
qa: lint testall

# Run all the tests, but allow for arguments to be passed
[group('test')]
test *ARGS:
	@echo "Running with arg: {{ ARGS }}"
	uv run --python={{ PYTHON_VERSION }} pytest {{ ARGS }}

# Run all the tests, but on failure, drop into the debugger
[group('test')]
pdb *ARGS:
	@echo "Running with arg: {{ ARGS }}"
	uv run --python={{ PYTHON_VERSION }} pytest --pdb --maxfail=10 {{ ARGS }}

# Run tests with coverage across all supported Python versions
[group('test')]
coverage:
	uv run --python={{ PYTHON_VERSION }} coverage run -m pytest
	uv run --python={{ PYTHON_VERSION }} coverage combine
	uv run --python={{ PYTHON_VERSION }} coverage report
	uv run --python={{ PYTHON_VERSION }} coverage html

# Serve docs locally with live reload
[group('doc')]
docs-serve:
	-lsof -ti :8000 | xargs kill
	uv run --group docs zensical serve

# Build docs (strict mode, fails on warnings)
[group('doc')]
docs-build:
	uv run --group docs zensical build --clean

# Build the project, useful for checking that packaging is correct
[group('package')]
build:
	rm -rf build
	rm -rf dist
	uv build

# Setup Python environment
[group('setup')]
requirements:
	uv sync --compile-bytecode

# Update Python dependencies
[group('maintain')]
update-deps:
	uv sync -U --compile-bytecode

# Tag, push, and create a GitHub release
[group('maintain')]
release:
	uv run scripts/release.py

# Remove all build, test, coverage and Python artifacts
[group('clean')]
clean:
	clean-build
	clean-pyc
	clean-test

# Remove build artifacts
[group('clean')]
clean-build:
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

# Remove Python file artifacts
[group('clean')]
clean-pyc:
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

# Remove test and coverage artifacts
[group('clean')]
clean-test:
	rm -f .coverage
	rm -f .coverage.*
	rm -fr htmlcov/
	rm -fr .pytest_cache

# Publish to PyPI
[group('maintain')]
publish:
	uv build
	uv publish
