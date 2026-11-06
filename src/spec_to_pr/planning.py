"""Planning and the approval gate (F4).

With planning on, the agent explores the repo and calls `submit_plan` with a plan.md before it may
edit anything. A human (or auto-approval in eval mode) then approves, edits or rejects the plan:

- approve: the agent implements the plan as written
- edit: the agent implements the human's edited plan
- reject: the run ends and no PR is opened

Any files changed during planning are reverted before implementation starts, so nothing is
edited before approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

REQUIRED_SECTIONS = ["Files to change", "Steps", "Tests to add", "Acceptance criteria"]

PLAN_TOOL = {
    "name": "submit_plan",
    "description": (
        "Submit plan.md for approval before making any edits. Markdown with these sections: "
        "'## Files to change', '## Steps', '## Tests to add', '## Acceptance criteria'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"plan": {"type": "string", "description": "The full plan.md in markdown."}},
        "required": ["plan"],
    },
}

PLANNING_PROMPT = """
Plan first (enforced by the harness):
1. Explore the repository with read_file, search and read-only commands. Do not edit anything yet.
2. Call submit_plan with plan.md: ## Files to change, ## Steps, ## Tests to add, ## Acceptance criteria.
   Be specific: name files and functions, and list the tests you will add.
3. A human approves, edits or rejects the plan. Edits and finish are refused until it is approved.
   Once approved, implement the approved plan; if it was edited, follow the edited version.
"""


def missing_sections(plan: str) -> list[str]:
    headings = {m.group(1).strip().lower() for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", plan, re.M)}
    return [s for s in REQUIRED_SECTIONS if s.lower() not in headings]


@dataclass
class Decision:
    action: str  # approve | edit | reject
    plan: str  # the plan to implement (the edited text for "edit")
    by: str = "human"  # human | auto
    note: str = ""


Approver = Callable[[str], Decision]


def auto_approve(plan: str) -> Decision:
    """Eval mode: approve every plan unchanged."""
    return Decision("approve", plan, by="auto")


@dataclass
class Planner:
    """Tracks the plan state for one run; the toolbox consults it to gate edits and finish."""

    plan: str | None = None
    decision: Decision | None = None

    @property
    def approved(self) -> bool:
        return self.decision is not None and self.decision.action in ("approve", "edit")

    def gate(self, tool: str) -> str | None:
        """Reason a tool is refused in the current phase, or None."""
        if not self.approved and tool in ("edit_file", "finish"):
            return "Plan first: call submit_plan with plan.md and wait for approval before editing or finishing."
        if self.approved and tool == "submit_plan":
            return "The plan is already approved; implement it."
        return None
