# Default sandbox image for Spec-to-PR tasks.
# The sandbox runs with --network none, so every tool the agent needs must be here.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==9.2.0

RUN git config --global user.email "agent@spec-to-pr.local" \
    && git config --global user.name "spec-to-pr" \
    && git config --global --add safe.directory /repo

# No .pyc caches: one compiled from the original code can be reused after a same-size edit made in
# the same second, so tests would run stale code.
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /repo
