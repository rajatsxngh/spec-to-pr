.PHONY: test validate eval smoke oracle image ablate

test:
	uv run pytest

image:
	uv run spec-to-pr build-image

validate:
	uv run spec-to-pr validate-tasks tasks/self

eval:
	uv run spec-to-pr eval tasks/self --config configs/bare.toml --runs 3

smoke:
	uv run spec-to-pr eval tasks/self --config configs/bare.toml --limit 5 --max-total-usd 5

# Ablation rungs built so far: bare -> +verification -> +skills.
ablate:
	uv run spec-to-pr eval tasks/self --config configs/bare.toml --runs 3
	uv run spec-to-pr eval tasks/self --config configs/verification.toml --runs 3
	uv run spec-to-pr eval tasks/self --config configs/skills.toml --runs 3

# Pipeline check with the reference solutions; no API calls.
oracle:
	uv run spec-to-pr eval tasks/self --agent oracle --max-total-usd 0
