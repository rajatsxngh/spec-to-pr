"""The mini-swe-agent baseline: the PRD's success metric is matching or beating it on the same tasks.

mini-swe-agent (https://github.com/SWE-agent/mini-swe-agent) is the SWE-bench team's minimal agent: one
bash tool, a linear message history, and a submit command. This adapter runs its real agent class
(`DefaultAgent`) with its real SWE-bench prompts, observation and format-error templates
(`config/benchmarks/swebench.yaml`), and plugs in two pieces through its documented protocols:

- `SandboxEnvironment` (its `Environment` protocol): every command goes through our sandbox's `exec`,
  so the baseline gets the same Docker container, limits and no-network policy as our agent. It keeps
  upstream's output dict, timeout message and `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` submit check.
- `SandboxModel` (its `Model` protocol): sends the conversation through our `ModelClient`, so both
  agents use the same model, effort, caching and pricing. Upstream would call LiteLLM here; this is
  only the transport. Tool calls are parsed with upstream's `parse_toolcall_actions`, so format errors
  behave as upstream.

Our budget caps (steps, tokens, dollars, wall clock) are checked before every model call, on top of
upstream's own step, cost and time limits set to the same values. The run writes the same trajectory,
report and patch files as our agent, plus upstream's own trajectory format (mini_trajectory.json), and
returns a `RunResult`, so the eval runner grades it with the hidden tests exactly as it grades our agent.

Needs the `baseline` extra (`uv sync --extra baseline`); the dev group installs it too.
"""

from __future__ import annotations

import json
import os
import shlex
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")  # no banner on import

import yaml  # noqa: E402  (installed with mini-swe-agent)
from minisweagent import __version__ as MINI_VERSION  # noqa: E402
from minisweagent import package_dir  # noqa: E402
from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.environments.local import LocalEnvironment  # noqa: E402
from minisweagent.exceptions import FormatError, LimitsExceeded  # noqa: E402
from minisweagent.models.utils.actions_toolcall import (  # noqa: E402
    BASH_TOOL,
    format_toolcall_observation_messages,
    parse_toolcall_actions,
)
from minisweagent.utils.serialize import recursive_merge  # noqa: E402

from .agent import RunResult, _diff, _ensure_git_baseline, _write_reports  # noqa: E402
from .budget import BudgetTracker  # noqa: E402
from .config import RunConfig  # noqa: E402
from .models import ModelClient  # noqa: E402
from .sandbox import Sandbox  # noqa: E402
from .trajectory import Trajectory, _jsonable  # noqa: E402
from .verification import detect_weakening  # noqa: E402

AGENT = "mini-swe-agent"
MINI_CONFIG = "benchmarks/swebench.yaml"  # upstream's SWE-bench config (tool calls, patch submission)
UPSTREAM_WORKDIR = "/testbed"  # the SWE-bench working directory its prompt names
SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

# Upstream's bash tool, in the Anthropic tool format.
ANTHROPIC_BASH_TOOL = {
    "name": BASH_TOOL["function"]["name"],
    "description": BASH_TOOL["function"]["description"],
    "input_schema": BASH_TOOL["function"]["parameters"],
}
# Anthropic stop reasons as the OpenAI finish reasons upstream's format-error template checks.
FINISH_REASONS = {"tool_use": "tool_calls", "max_tokens": "length", "end_turn": "stop", "stop_sequence": "stop",
                  "refusal": "content_filter", "pause_turn": "stop"}
EXIT_STATUS = {"Submitted": "finished", "LimitsExceeded": "budget_exceeded", "TimeExceeded": "budget_exceeded",
               "RepeatedFormatError": "stopped_without_finish"}


def load_mini_config(name: str = MINI_CONFIG) -> dict[str, Any]:
    """One of the YAML configs shipped inside the mini-swe-agent package."""
    return yaml.safe_load((Path(package_dir) / "config" / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- environment


class SandboxEnvironment(LocalEnvironment):
    """mini-swe-agent's `Environment`, backed by a Spec-to-PR sandbox instead of its own subprocess or
    Docker handling. Subclasses `LocalEnvironment` for its config and upstream's submit check."""

    def __init__(self, sandbox: Sandbox, log: Trajectory | None = None, step: Any = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.sandbox = sandbox
        self.log = log
        self._step = step or (lambda: 0)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        command = action.get("command", "")
        timeout = timeout or self.config.timeout
        # Each action runs in a fresh shell, as upstream: env vars first, then the working directory.
        prefix = "".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in self.config.env.items())
        if cwd or self.config.cwd:
            prefix += f"cd {shlex.quote(cwd or self.config.cwd)}\n"
        t0 = time.monotonic()
        try:
            r = self.sandbox.exec(prefix + command, timeout_s=timeout)
            if r.timed_out:  # upstream reports a timeout as returncode -1 with subprocess's message
                msg = f"Command '{command}' timed out after {timeout} seconds"
                output = {"output": r.output, "returncode": -1,
                          "exception_info": f"An error occurred while executing the command: {msg}",
                          "extra": {"exception_type": "TimeoutExpired", "exception": msg}}
            else:
                output = {"output": r.output, "returncode": r.exit_code, "exception_info": ""}
        except Exception as e:
            output = {"output": "", "returncode": -1,
                      "exception_info": f"An error occurred while executing the command: {e}",
                      "extra": {"exception_type": type(e).__name__, "exception": str(e)}}
        if self.log is not None:
            self.log.write("tool_call", step=self._step(), tool="bash", input={"command": command},
                           output=output["output"], is_error=output["returncode"] != 0,
                           duration_s=round(time.monotonic() - t0, 3))
        self._check_finished(output)  # raises Submitted on the submit command
        return output

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        # Upstream also merges the host's os.environ and uname here; the sandbox is not the host.
        return recursive_merge(self.config.model_dump(), kwargs)


# ---------------------------------------------------------------- model


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))


def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """mini-swe-agent's OpenAI-style history as an Anthropic system prompt and message list. Assistant
    turns are replayed from the exact blocks returned (so thinking blocks stay valid); tool results go
    back as tool_result blocks; consecutive turns of one role are merged."""
    system: list[str] = []
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system.append(_text(m.get("content")))
            continue
        if role == "assistant":
            blocks = [dict(b) for b in m.get("extra", {}).get("anthropic_content") or []]
            if not blocks:  # a message not produced by SandboxModel (e.g. a replayed trajectory)
                if _text(m.get("content")):
                    blocks.append({"type": "text", "text": _text(m.get("content"))})
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                                   "input": json.loads(tc["function"]["arguments"])})
            r = "assistant"
        elif role == "tool":
            blocks = [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": _text(m.get("content"))}]
            r = "user"
        elif role == "user":
            blocks = [{"type": "text", "text": _text(m.get("content")) or "(empty)"}]
            r = "user"
        else:  # "exit" and anything else is bookkeeping, never sent
            continue
        if out and out[-1]["role"] == r:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": r, "content": blocks})
    return "\n\n".join(system), out


class SandboxModel:
    """mini-swe-agent's `Model`, sending each query through a Spec-to-PR `ModelClient`."""

    def __init__(self, client: ModelClient, tracker: BudgetTracker, log: Trajectory | None = None,
                 observation_template: str | None = None, format_error_template: str = "{{ error }}"):
        self.client = client
        self.tracker = tracker
        self.log = log
        self.config = SimpleNamespace(
            model_name=client.name,
            observation_template=observation_template or (
                "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
                "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"),
            format_error_template=format_error_template,
        )
        self.models_used: list[str] = []

    def _dump(self) -> dict[str, Any]:
        return dict(vars(self.config))

    def query(self, messages: list[dict], **kwargs: Any) -> dict:
        system, msgs = to_anthropic(messages)
        resp = self.client.complete(system, msgs, [ANTHROPIC_BASH_TOOL])
        self.tracker.record(resp.usage, resp.cost_usd)
        if resp.model not in self.models_used:
            self.models_used.append(resp.model)
        content = [_jsonable(b) for b in resp.content]
        if self.log is not None:
            self.log.write("model_call", step=self.tracker.steps, model=resp.model, stop_reason=resp.stop_reason,
                           usage=resp.usage.to_dict(), cost_usd=round(resp.cost_usd, 6),
                           request_id=resp.request_id, content=content)
        tool_calls = [{"id": c.id, "type": "function",
                       "function": {"name": c.name, "arguments": json.dumps(c.input)}} for c in resp.tool_calls]
        extra = {"cost": resp.cost_usd, "usage": resp.usage.to_dict(), "model": resp.model,
                 "stop_reason": resp.stop_reason, "anthropic_content": content, "timestamp": time.time()}
        try:
            actions = parse_toolcall_actions(
                [SimpleNamespace(id=tc["id"], function=SimpleNamespace(**tc["function"])) for tc in tool_calls],
                format_error_template=self.config.format_error_template,
                template_kwargs={"finish_reason": FINISH_REASONS.get(resp.stop_reason, resp.stop_reason)},
            )
        except FormatError as e:  # as upstream: the bad turn is dropped, but its cost and response are kept
            e.messages[0]["extra"].update({"cost": resp.cost_usd, "response": extra})
            raise
        return {"role": "assistant", "content": resp.text or None, "tool_calls": tool_calls,
                "extra": {"actions": actions, **extra}}

    def format_message(self, **kwargs: Any) -> dict:
        return kwargs

    def format_observation_messages(self, message: dict, outputs: list[dict],
                                    template_vars: dict | None = None) -> list[dict]:
        return format_toolcall_observation_messages(
            actions=message.get("extra", {}).get("actions", []), outputs=outputs,
            observation_template=self.config.observation_template, template_vars=template_vars)

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return self._dump()

    def serialize(self) -> dict:
        return {"info": {"config": {"model": self._dump(),
                                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}"}}}


# ---------------------------------------------------------------- agent


class BudgetedAgent(DefaultAgent):
    """Upstream's `DefaultAgent`, with our budget caps checked before each model call."""

    def __init__(self, model: Any, env: Any, tracker: BudgetTracker, **kwargs: Any):
        super().__init__(model, env, **kwargs)
        self.tracker = tracker

    def query(self) -> dict:
        over = self.tracker.exceeded()
        if over:
            raise LimitsExceeded({"role": "exit", "content": "LimitsExceeded",
                                  "extra": {"exit_status": "LimitsExceeded", "submission": "",
                                            "detail": f"Stopped: {over} reached"}})
        return super().query()


def run_baseline(task: str, client: ModelClient, sandbox: Sandbox, config: RunConfig, out_dir: Path,
                 grade_on: str = "worktree", mini_config: dict[str, Any] | None = None) -> RunResult:
    """Run mini-swe-agent on one task in `sandbox`. `grade_on`:
    - "worktree": leave the sandbox as the agent left it, the same state our agent is graded on;
    - "submission": reset to the start and apply only the patch the agent submitted, as SWE-bench
      grades mini-swe-agent (no submission means no changes)."""
    if grade_on not in ("worktree", "submission"):
        raise ValueError(f"grade_on must be worktree or submission, not {grade_on!r}")
    raw = mini_config or load_mini_config()
    out_dir.mkdir(parents=True, exist_ok=True)
    log = Trajectory(out_dir / "trajectory.jsonl")
    tracker = BudgetTracker(config.budget)
    _ensure_git_baseline(sandbox)
    workdir = sandbox.exec("pwd", timeout_s=30).output.strip() or "."

    b = config.budget
    agent_cfg = {k: (v.replace(UPSTREAM_WORKDIR, workdir) if isinstance(v, str) else v)
                 for k, v in raw["agent"].items()}
    agent_cfg.update(step_limit=b.max_steps, cost_limit=b.max_usd, wall_time_limit_seconds=int(b.max_seconds),
                     output_path=None)
    env_vars = {k: v for k, v in raw.get("environment", {}).get("env", {}).items()
                if k != "BASH_ENV"}  # BASH_ENV activates SWE-bench's conda env, which our image doesn't have
    env = SandboxEnvironment(sandbox, log, step=lambda: tracker.steps, cwd="",
                             timeout=config.sandbox.command_timeout_s, env=env_vars)
    model_cfg = raw.get("model", {})
    model = SandboxModel(client, tracker, log, observation_template=model_cfg.get("observation_template"),
                         format_error_template=model_cfg.get("format_error_template", "{{ error }}"))
    agent = BudgetedAgent(model, env, tracker, **agent_cfg)
    log.write("run_start", task=task, model=client.name, agent=AGENT, mini_version=MINI_VERSION,
              mini_config=MINI_CONFIG if mini_config is None else "custom", grade_on=grade_on,
              config=asdict(config))

    status, detail, exit_status, submission = "error", "", "", ""
    try:
        info = agent.run(task)
        exit_status, submission = info.get("exit_status", ""), info.get("submission", "") or ""
        status = EXIT_STATUS.get(exit_status, "error")
        detail = info.get("detail") or f"mini-swe-agent exit status: {exit_status}"
    except Exception as e:  # a failed model call, as in our agent: stop and report what we have
        exit_status = type(e).__name__
        detail = f"Model call failed: {type(e).__name__}: {e}"
        log.write("error", step=tracker.steps, message=detail)
    finally:
        if grade_on == "submission":
            _apply_submission(sandbox, submission if status == "finished" else "", log)
        flags = detect_weakening(sandbox)
        if flags:
            log.write("weakening_flags", flags=flags)
        diff = _diff(sandbox)
        data = agent.serialize({"info": {"exit_status": exit_status, "submission": submission}})
        (out_dir / "mini_trajectory.json").write_text(json.dumps(data, indent=2, default=_jsonable),
                                                      encoding="utf-8")
        if submission:
            (out_dir / "submission.patch").write_text(submission, encoding="utf-8")
        summary = f"mini-swe-agent {exit_status or 'stopped'}" + (
            f", submitted a {len(submission)}-byte patch" if submission else "")
        result = RunResult(status, summary, detail, tracker.snapshot(), diff, out_dir, model.models_used, flags)
        log.write("run_end", agent=AGENT, exit_status=exit_status, **result.to_dict())
        log.close()
        _write_reports(result, task)
    return result


def _apply_submission(sandbox: Sandbox, patch: str, log: Trajectory) -> None:
    sandbox.exec("git reset -q --hard HEAD && git clean -fdq", timeout_s=60)
    if not patch.strip():
        log.write("submission", applied=False, reason="no submission")
        return
    name = ".mswea_submission.patch"
    sandbox.write_file(name, patch if patch.endswith("\n") else patch + "\n")
    r = sandbox.exec(f"git apply --whitespace=nowarn {name}; rc=$?; rm -f {name}; exit $rc", timeout_s=60)
    log.write("submission", applied=r.exit_code == 0, output=r.output[-2000:])
