"""Skills (F8): instruction packs the agent loads on demand, plus the target repo's AGENTS.md.

Each skill is `skills/<name>/SKILL.md` with a frontmatter block:

    ---
    name: python-testing
    description: One line saying when to load it.
    ---
    The instructions.

Only names and descriptions go in the system prompt; the body enters context when the agent calls
`load_skill`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .sandbox import Sandbox

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
MAX_AGENTS_MD_CHARS = 8_000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def parse_skill(text: str, fallback_name: str) -> Skill:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return Skill(meta.get("name", fallback_name), meta.get("description", ""), body.strip())


def load_skills(directory: Path | None) -> dict[str, Skill]:
    directory = directory or DEFAULT_SKILLS_DIR
    skills = {}
    for f in sorted(directory.glob("*/SKILL.md")):
        s = parse_skill(f.read_text(encoding="utf-8"), f.parent.name)
        skills[s.name] = s
    return skills


def skills_prompt(skills: dict[str, Skill]) -> str:
    if not skills:
        return ""
    lines = "\n".join(f"- {s.name}: {s.description}" for s in skills.values())
    return f"\nSkills you can load with load_skill when they apply:\n{lines}\n"


def agents_md(sandbox: Sandbox) -> str | None:
    try:
        text = sandbox.read_file("AGENTS.md")
    except (FileNotFoundError, OSError, PermissionError):
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > MAX_AGENTS_MD_CHARS:
        text = text[:MAX_AGENTS_MD_CHARS] + "\n[... truncated ...]"
    return text
