"""The agent's five tools (F1): run, read_file, edit_file, search, finish."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from .sandbox import Sandbox

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "run",
        "description": (
            "Run a bash command in the repository root inside the sandbox and return its exit code "
            "and combined output. Use it for tests, builds and inspection. The sandbox has no network."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to run."},
                "timeout_s": {"type": "integer", "description": "Seconds before the command is killed (default 120)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file, with line numbers. Optionally limit to a line range (1-based, inclusive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repository root."},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace one exact occurrence of old_str with new_str in a file. old_str must match the file "
            "exactly once, including whitespace; include enough surrounding lines to make it unique. "
            "To create a new file, pass an empty old_str and the full content as new_str."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "search",
        "description": "Search file contents with ripgrep (regex). Returns matching lines as path:line:text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Directory or file to search (default: repository root)."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "finish",
        "description": "Call when the task is complete. Summarize what you changed and how you verified it.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]


FINISH_VERIFIED = {
    "name": "finish",
    "description": (
        "Call when the task is complete. The harness checks that your test fails without your source "
        "change and passes with it; if a check can't be met, explain why in `explanation`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "What you changed and how you verified it."},
            "test_command": {"type": "string", "description": "Command that runs the relevant tests."},
            "explanation": {"type": "string", "description": "Only if a check can't be met: why."},
        },
        "required": ["summary", "test_command"],
    },
}

LOAD_SKILL = {
    "name": "load_skill",
    "description": "Load the full instructions of one of the listed skills.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
}


def tool_schemas(verification: bool = False, skills: bool = False) -> list[dict[str, Any]]:
    schemas = [s for s in TOOL_SCHEMAS if not (verification and s["name"] == "finish")]
    if verification:
        schemas.append(FINISH_VERIFIED)
    if skills:
        schemas.append(LOAD_SKILL)
    return schemas


@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    finished: bool = False


class ToolError(Exception):
    pass


def truncate(text: str, limit: int) -> str:
    """Keep the head and tail of long output with a marker in between."""
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n\n[... {dropped} characters omitted ...]\n\n{text[-half:]}"


class Toolbox:
    def __init__(self, sandbox: Sandbox, default_timeout_s: int = 120, max_output_chars: int = 20_000,
                 verifier: Any = None, skills: dict[str, Any] | None = None):
        self.sandbox = sandbox
        self.default_timeout_s = default_timeout_s
        self.max_output_chars = max_output_chars
        self.verifier = verifier  # verification.Verifier when F7 is on
        self.skills = skills  # name -> skills.Skill when F8 is on

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolResult(f"Unknown tool: {name}", is_error=True)
        try:
            result = handler(**args)
        except TypeError as e:
            return ToolResult(f"Bad arguments for {name}: {e}", is_error=True)
        except (ToolError, OSError, PermissionError) as e:
            return ToolResult(f"{type(e).__name__}: {e}", is_error=True)
        result.output = truncate(result.output, self.max_output_chars)
        return result

    def _tool_run(self, command: str, timeout_s: int | None = None) -> ToolResult:
        timeout = min(int(timeout_s or self.default_timeout_s), self.default_timeout_s)
        r = self.sandbox.exec(command, timeout_s=timeout)
        status = f"exit code {r.exit_code}" + (" (timed out)" if r.timed_out else "")
        # A non-zero exit (e.g. failing tests) is information, not a tool failure.
        return ToolResult(f"[{status}]\n{r.output}", is_error=r.timed_out)

    def _tool_read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
        try:
            text = self.sandbox.read_file(path)
        except FileNotFoundError:
            raise ToolError(f"{path} does not exist")
        lines = text.splitlines()
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        numbered = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(start, end + 1))
        return ToolResult(numbered or "(empty file)")

    def _tool_edit_file(self, path: str, old_str: str, new_str: str) -> ToolResult:
        if self.verifier is not None:
            refusal = self.verifier.allows_source_edit(path)
            if refusal:
                raise ToolError(refusal)
        if old_str == "":
            try:
                self.sandbox.read_file(path)
            except FileNotFoundError:
                self.sandbox.write_file(path, new_str)
                return ToolResult(f"Created {path}")
            raise ToolError(f"{path} already exists; pass the text to replace as old_str")
        try:
            text = self.sandbox.read_file(path)
        except FileNotFoundError:
            raise ToolError(f"{path} does not exist")
        count = text.count(old_str)
        if count == 0:
            raise ToolError(f"old_str not found in {path}; read the file and copy the text exactly")
        if count > 1:
            raise ToolError(f"old_str matches {count} places in {path}; include more surrounding lines")
        self.sandbox.write_file(path, text.replace(old_str, new_str, 1))
        return ToolResult(f"Edited {path}")

    def _tool_search(self, pattern: str, path: str = ".") -> ToolResult:
        q = shlex.quote
        cmd = (
            f"if command -v rg >/dev/null; then rg -n --no-heading --color never -e {q(pattern)} -- {q(path)}; "
            f"else grep -rnE -- {q(pattern)} {q(path)}; fi"
        )
        r = self.sandbox.exec(cmd, timeout_s=self.default_timeout_s)
        if r.exit_code == 1 and not r.output.strip():
            return ToolResult("No matches.")
        return ToolResult(r.output, is_error=r.exit_code not in (0, 1))

    def _tool_finish(self, summary: str, test_command: str | None = None, explanation: str | None = None) -> ToolResult:
        if self.verifier is None:
            return ToolResult(summary, finished=True)
        check = self.verifier.check_finish(test_command, explanation)
        if not check.accepted:
            return ToolResult(check.message, is_error=True)
        return ToolResult(summary, finished=True)

    def _tool_load_skill(self, name: str) -> ToolResult:
        if self.skills is None:
            raise ToolError("Skills are not enabled")
        skill = self.skills.get(name)
        if skill is None:
            raise ToolError(f"No skill named {name!r}; available: {', '.join(self.skills) or 'none'}")
        return ToolResult(f"# Skill: {skill.name}\n\n{skill.body}")
