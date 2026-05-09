IMAGE_NAME ?= tagging-ms
IMAGE_TAG  ?= latest
ENV_FILE   ?= .env
GIT_SHA    ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

.PHONY: help install build run stop clean lint format typecheck test check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Sync dependencies (incl. dev)
	uv sync

lint: ## Run ruff lint checks
	uv run ruff check src tests

format: ## Run black + isort
	uv run black src tests
	uv run isort src tests

typecheck: ## Run mypy
	uv run mypy src

test: ## Run pytest (cassettes only, no live API calls)
	uv run pytest

check: lint typecheck test ## Run lint, typecheck, and tests

build: ## Build the Docker image (passes GIT_SHA build arg)
	docker build --build-arg GIT_SHA=$(GIT_SHA) -t $(IMAGE_NAME):$(IMAGE_TAG) .

run: ## Run the container (reads .env, publishes port from env or default 8000)
	docker run --rm \
		--env-file $(ENV_FILE) \
		-p 8000:$$(grep -E '^TAGGING_MS_PORT=' $(ENV_FILE) | cut -d= -f2 | tr -d ' ') \
		--name $(IMAGE_NAME) \
		$(IMAGE_NAME):$(IMAGE_TAG)

stop: ## Stop the running container
	docker stop $(IMAGE_NAME)

clean: ## Remove build artifacts and caches
	rm -rf .venv
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -prune -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
