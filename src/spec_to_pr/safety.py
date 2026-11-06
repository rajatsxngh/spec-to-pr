"""Safety (N1): a dangerous-command denylist and secret scrubbing.

The Docker sandbox is the main boundary (no network, no host env). The denylist is a second layer
that refuses commands with no legitimate use inside a task, and is the only layer for the local
development sandbox.
"""

from __future__ import annotations

import re

# Start of a shell command: line start or after ; & | ( or `, optionally behind sudo/env/exec.
CMD = r"(?:^|[;&|(`]|\$\()\s*(?:sudo\s+|env\s+|exec\s+)*"
GIT = CMD + r"git\s+(?:-[-\w]+(?:[ =]\S+)?\s+)*"

DENYLIST: list[tuple[str, re.Pattern[str]]] = [
    ("git push", re.compile(GIT + r"push\b", re.M)),
    ("changing git remotes", re.compile(GIT + r"remote\s+(add|set-url|rename|remove|rm)\b", re.M)),
    ("rewriting git credentials config", re.compile(GIT + r"config\b[^;&|\n]*credential", re.M)),
    ("deleting the filesystem root or home", re.compile(CMD + r"rm\s+(-[a-zA-Z-]+\s+)*(/|~|\$HOME)/?\*?(\s|$)", re.M)),
    ("formatting disks", re.compile(CMD + r"(mkfs(\.\w+)?|fdisk|wipefs)\b", re.M)),
    ("writing to a block device", re.compile(CMD + r"dd\b[^;&|\n]*\bof=/dev/(?!null\b)", re.M)),
    ("fork bomb", re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    ("shutting down the machine", re.compile(CMD + r"(shutdown|reboot|halt|poweroff)\b", re.M)),
    ("piping a download into a shell", re.compile(r"\b(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")),
    ("opening up permissions on /", re.compile(r"\bchmod\s+-R\s+0?777\s+/(\s|$)")),
    ("reading host credential stores", re.compile(r"(~|\$HOME)/\.(ssh|aws|config/gh|netrc|git-credentials)\b")),
]


def blocked_reason(command: str) -> str | None:
    """Name of the first denylist rule the command matches, or None if it may run."""
    for name, pattern in DENYLIST:
        if pattern.search(command):
            return name
    return None


SECRET_NAME = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL)", re.I)
SECRET_EXACT = {"GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
                "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "LANGFUSE_SECRET_KEY", "OTEL_EXPORTER_OTLP_HEADERS"}


def scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Drop variables that look like secrets."""
    return {k: v for k, v in env.items() if k.upper() not in SECRET_EXACT and not SECRET_NAME.search(k)}
