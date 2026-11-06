.PHONY: test check validate eval smoke oracle image ablate injection

test:
	uv run pytest

# Integration checks across all components and configs (run after every milestone).
check:
	uv run python scripts/check.py

image:
	uv run spec-to-pr build-image

validate:
	uv run spec-to-pr validate-tasks tasks/self
	uv run spec-to-pr validate-tasks tasks/injection

# Prompt-injection test (N3): planted instructions must be ignored.
injection:
	uv run spec-to-pr eval tasks/injection --config configs/context.toml --runs 3

eval:
	uv run spec-to-pr eval tasks/self --config configs/bare.toml --runs 3

smoke:
	uv run spec-to-pr eval tasks/self --config configs/bare.toml --limit 5 --max-total-usd 5

# Full ablation ladder: bare -> +verification -> +skills -> +planning -> +context (full harness).
ablate:
	uv run spec-to-pr eval tasks/self --config configs/bare.toml --runs 3
	uv run spec-to-pr eval tasks/self --config configs/verification.toml --runs 3
	uv run spec-to-pr eval tasks/self --config configs/skills.toml --runs 3
	uv run spec-to-pr eval tasks/self --config configs/planning.toml --runs 3
	uv run spec-to-pr eval tasks/self --config configs/context.toml --runs 3

# Pipeline check with the reference solutions; no API calls.
oracle:
	uv run spec-to-pr eval tasks/self --agent oracle --max-total-usd 0
