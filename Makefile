IMAGE_NAME ?= tagging-ms
IMAGE_TAG  ?= latest
ENV_FILE   ?= .env

.PHONY: help build run stop clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

build: ## Build the Docker image
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

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
	find . -type d -name ".streamlit" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
