"""SWE-bench Live importer: real, recent GitHub issues as eval tasks (N5).

Source: the `SWE-bench-Live/SWE-bench-Live` dataset on Hugging Face (fields documented on its card:
instance_id, repo, base_commit, problem_statement, patch, test_patch, FAIL_TO_PASS, PASS_TO_PASS,
test_cmds, log_parser, rebuild_cmds, docker_image, created_at, ...), graded the way
microsoft/SWE-bench-Live `evaluation/evaluation.py` grades.

Each instance becomes an ordinary task in our format, with three differences:

- `workdir = "/testbed"`: the instance's Docker image already holds the repo at the base commit, so
  the sandbox works on that checkout instead of copying a `repo/` directory in.
- `hidden/.swebench_live/` holds the grading kit: the test patch, the FAIL_TO_PASS / PASS_TO_PASS
  lists, the instance's test command, and `grade.py` (a copy of `swebench_grade.py`). The task's
  `test_cmd` runs `run.sh`, which restores every file the test patch touches to the base commit
  (so edits the agent made to those tests don't count), applies the test patch, runs the
  instance's own test command, and grades the log: resolved = every FAIL_TO_PASS test passes and
  no PASS_TO_PASS test fails.
- `solution.patch` is the gold patch, applied with `git apply` to validate the task.

The agent sees only the problem statement and the container, as the SWE-bench protocol requires.
"""

from __future__ import annotations

import json
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

DATASET = "SWE-bench-Live/SWE-bench-Live"
DATASETS_SERVER = "https://datasets-server.huggingface.co"
DOCKER_HUB = "https://hub.docker.com/v2/repositories"
WORKDIR = "/testbed"
KIT = ".swebench_live"  # grading kit directory, copied into the repo only at grading time
GRADER = Path(__file__).with_name("swebench_grade.py")


class ImportError_(RuntimeError):  # noqa: N801 - avoid shadowing the builtin ImportError
    pass


# ---------------------------------------------------------------- fetching


def fetch_instances(since: str, split: str = "full", dataset: str = DATASET, page: int = 100,
                    get: Callable[..., Any] | None = None, retries: int = 3) -> Iterator[dict]:
    """Instances created on or after `since` (YYYY-MM-DD), newest first, from the Hugging Face
    datasets server (no download of the whole parquet).

    Uses the server's /filter endpoint; when that fails (it often returns a transient 500), falls
    back to paging /rows backwards from the end of the split, which is in chronological order."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        raise ValueError(f"since must be YYYY-MM-DD, got {since!r}")
    if get is None:
        import httpx

        client = httpx.Client(timeout=120, headers={"User-Agent": "spec-to-pr-importer"})
        get = client.get
    base = {"dataset": dataset, "config": "default", "split": split}
    offset = 0
    while True:
        params = {**base, "where": f"\"created_at\">='{since}'", "orderby": "\"created_at\" DESC",
                  "offset": offset, "length": page}
        try:
            data = _get_json(get, f"{DATASETS_SERVER}/filter", params, retries)
        except ImportError_:
            if offset:
                raise
            yield from _rows_backwards(get, base, since, page, retries)
            return
        rows = [r["row"] for r in data.get("rows", [])]
        yield from rows
        offset += len(rows)
        if not rows or offset >= data.get("num_rows_total", 0):
            return


def _rows_backwards(get: Callable[..., Any], base: dict, since: str, page: int, retries: int) -> Iterator[dict]:
    first = _get_json(get, f"{DATASETS_SERVER}/rows", {**base, "offset": 0, "length": 1}, retries * 2)
    end = first["num_rows_total"]
    while end > 0:
        start = max(0, end - page)
        data = _get_json(get, f"{DATASETS_SERVER}/rows", {**base, "offset": start, "length": end - start},
                         retries * 2)
        rows = sorted((r["row"] for r in data.get("rows", [])), key=lambda r: str(r.get("created_at", "")),
                      reverse=True)
        recent = [r for r in rows if str(r.get("created_at", ""))[:10] >= since]
        yield from recent
        if not recent:  # a whole page older than `since`: everything before it is older too
            return
        end = start


def _get_json(get: Callable[..., Any], url: str, params: dict | None, retries: int) -> dict:
    last = ""
    for attempt in range(retries):
        try:
            resp = get(url, params=params)
            data = resp.json()
            if resp.status_code == 200 and "error" not in data:
                return data
            last = f"HTTP {resp.status_code}: {str(data.get('error', data))[:200]}"
        except Exception as e:  # network errors and bad JSON: the datasets server is flaky
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(60, 5 * (attempt + 1)))
    raise ImportError_(f"{url} failed after {retries} attempts: {last}")


def load_jsonl(path: Path) -> list[dict]:
    """Instances from a local JSONL file (one instance per line), e.g. an exported split."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def image_size_gb(image: str, get: Callable[..., Any] | None = None) -> float | None:
    """Compressed size of the image's `latest` tag on Docker Hub, or None if unknown."""
    if get is None:
        import httpx

        get = httpx.Client(timeout=30).get
    repo, _, tag = image.partition(":")
    try:
        resp = get(f"{DOCKER_HUB}/{repo}/tags/{tag or 'latest'}", params=None)
        if resp.status_code != 200:
            return None
        return resp.json()["full_size"] / 1e9
    except Exception:
        return None


# ---------------------------------------------------------------- selection


@dataclass
class Rules:
    """Which instances make good tasks for this harness."""

    since: str = "2025-06-01"
    max_image_gb: float = 1.5  # compressed size on Docker Hub; unpacked is about 4x
    per_repo: int = 2  # spread the set over many repos
    max_fail_to_pass: int = 50
    max_pass_to_pass: int = 5000  # huge suites take too long to run per task
    exclude: set[str] = field(default_factory=set)


def static_reject(inst: dict, rules: Rules) -> str | None:
    """Why an instance can't be used, judged from its fields alone (None when it can)."""
    if str(inst.get("created_at", ""))[:10] < rules.since:
        return f"created {str(inst.get('created_at'))[:10]}, before {rules.since}"
    if inst["instance_id"] in rules.exclude:
        return "excluded"
    if (inst.get("log_parser") or "").strip().lower() != "pytest":
        return f"log parser {inst.get('log_parser')!r} (only pytest is supported)"
    if not inst.get("docker_image"):
        return "no docker image"
    if not inst.get("FAIL_TO_PASS"):
        return "no FAIL_TO_PASS tests"
    if len(inst["FAIL_TO_PASS"]) > rules.max_fail_to_pass:
        return f"{len(inst['FAIL_TO_PASS'])} FAIL_TO_PASS tests"
    if len(inst.get("PASS_TO_PASS") or []) > rules.max_pass_to_pass:
        return f"{len(inst['PASS_TO_PASS'])} PASS_TO_PASS tests"
    if not inst.get("test_cmds"):
        return "no test command"
    for name in ("patch", "test_patch"):
        text = inst.get(name) or ""
        if "GIT binary patch" in text or "Binary files" in text:
            return f"binary {name}"
        if "\r" in text:
            return f"carriage returns in {name}"  # task files are read as text, which drops them
    if not patch_files(inst["test_patch"]):
        return "empty test patch"
    return None


def candidates(instances: Iterable[dict], rules: Rules, size_of: Callable[[str], float | None],
               on_reject: Callable[[dict, str], None] | None = None,
               per_repo: dict[str, int] | None = None) -> Iterator[dict]:
    """Instances that pass the static rules, the per-repo cap and the image-size cap, in input order.
    `per_repo` counts tasks already taken from each repo. It is incremented before a candidate is
    yielded, so a consumer that rejects the candidate should decrement it."""
    per_repo = {} if per_repo is None else per_repo
    for inst in instances:
        reason = static_reject(inst, rules)
        if reason is None and per_repo.get(inst["repo"], 0) >= rules.per_repo:
            reason = f"already {rules.per_repo} tasks from {inst['repo']}"
        if reason is None:
            size = size_of(inst["docker_image"])
            inst["_image_gb"] = size
            if size is None:
                reason = "image size unknown (not on Docker Hub?)"
            elif size > rules.max_image_gb:
                reason = f"image {size:.2f} GB > {rules.max_image_gb} GB"
        if reason:
            if on_reject:
                on_reject(inst, reason)
            continue
        per_repo[inst["repo"]] = per_repo.get(inst["repo"], 0) + 1
        yield inst


# ---------------------------------------------------------------- task files


def patch_files(patch: str) -> list[str]:
    """Every path a unified git diff touches (both sides of renames), in order."""
    files: list[str] = []
    for line in patch.splitlines():
        m = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if m:
            for p in m.groups():
                if p not in files:
                    files.append(p)
    return files


RUN_SH = """#!/bin/bash
# Grades SWE-bench Live instance {instance_id} the way its official harness does.
# Generated by `spec-to-pr import-swebench-live`; do not edit.
cd "$(dirname "$0")/.." || exit 2
KIT={kit}
BASE={base_commit}
# Grade on the original tests: restore every file the test patch touches, then apply it.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if git cat-file -e "$BASE:$f" 2>/dev/null; then git checkout -q "$BASE" -- "$f"; else rm -rf -- "$f"; fi
done < "$KIT/test_files.txt"
git apply --whitespace=nowarn "$KIT/test.patch" 2>/dev/null \\
  || git apply --reject --whitespace=nowarn "$KIT/test.patch" \\
  || {{ echo "test patch did not apply"; exit 2; }}
{rebuild}bash "$KIT/run_test.sh" > "$KIT/testlog.out" 2>&1
echo "--- test log (tail) ---"
tail -n 60 "$KIT/testlog.out"
echo "--- grade ---"
PY=$(command -v python || command -v python3)
"$PY" "$KIT/grade.py" "$KIT/testlog.out" "$KIT/tests.json"
"""


def task_toml(inst: dict, timeout_s: int) -> str:
    meta = {
        "instance_id": inst["instance_id"],
        "repo": inst["repo"],
        "pull_number": str(inst.get("pull_number", "")),
        "base_commit": inst["base_commit"],
        "created_at": str(inst.get("created_at", "")),
        "docker_image": inst["docker_image"],
        "test_cmds": list(inst["test_cmds"]),
        "fail_to_pass": len(inst["FAIL_TO_PASS"]),
        "pass_to_pass": len(inst.get("PASS_TO_PASS") or []),
    }
    if inst.get("_image_gb") is not None:
        meta["image_gb_compressed"] = round(inst["_image_gb"], 3)
    lines = [
        f"id = {_q(inst['instance_id'])}",
        f"image = {_q(inst['docker_image'])}",
        f"workdir = {_q(WORKDIR)}",
        f"test_cmd = {_q(f'bash {KIT}/run.sh')}",
        f"timeout_s = {timeout_s}",
        "",
        f"statement = {_q_multiline(inst['problem_statement'])}",
        "",
        "[swebench_live]",
        *(f"{k} = {_toml_value(v)}" for k, v in meta.items()),
        "",
    ]
    return "\n".join(lines)


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def _q_multiline(s: str) -> str:
    # A TOML literal multi-line string can't hold ''' ; fall back to an escaped basic string.
    body = s.replace("\r\n", "\n").strip()
    return f"'''\n{body}\n'''" if "'''" not in body and not body.endswith("'") else _q(body)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return _q(str(v))


def write_task(inst: dict, tasks_dir: Path, timeout_s: int = 1800) -> Path:
    """Write one instance as a task directory; returns it."""
    d = tasks_dir / inst["instance_id"]
    kit = d / "hidden" / KIT
    kit.mkdir(parents=True, exist_ok=True)
    (d / "task.toml").write_text(task_toml(inst, timeout_s), encoding="utf-8")
    _write(d / "solution.patch", inst["patch"])
    _write(kit / "test.patch", inst["test_patch"])
    _write(kit / "test_files.txt", "\n".join(patch_files(inst["test_patch"])) + "\n")
    _write(kit / "tests.json", json.dumps({"FAIL_TO_PASS": list(inst["FAIL_TO_PASS"]),
                                            "PASS_TO_PASS": list(inst.get("PASS_TO_PASS") or [])}, indent=1))
    # Same as the official harness: test_cmds joined with " ; " in a script run from the repo root.
    _write(kit / "run_test.sh", " ; ".join(inst["test_cmds"]) + "\n")
    rebuild = " ; ".join(inst.get("rebuild_cmds") or [])
    _write(kit / "run.sh", RUN_SH.format(instance_id=inst["instance_id"], kit=KIT, base_commit=inst["base_commit"],
                                          rebuild=f"{rebuild}\n" if rebuild.strip() else ""))
    _write(kit / "grade.py", GRADER.read_text(encoding="utf-8"))
    return d


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def write_taskset(tasks_dir: Path, task_ids: list[str], version: str, notes: str = "") -> None:
    ids = "\n".join(f"  {_q(t)}," for t in task_ids)
    desc = "Real recent GitHub issues from SWE-bench Live, run in each instance's own Docker image."
    text = (f'name = "swebench-live"\nversion = {_q(version)}\ndescription = {_q(desc)}\n'
            f"source = {_q(DATASET)}\n" + (f"notes = {_q(notes)}\n" if notes else "") + f"\ntasks = [\n{ids}\n]\n")
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "taskset.toml").write_text(text, encoding="utf-8")


def load_instance_image(tasks_dir: Path, task_id: str) -> str:
    return tomllib.loads((tasks_dir / task_id / "task.toml").read_text(encoding="utf-8"))["image"]


def existing_ids(tasks_dir: Path) -> list[str]:
    path = tasks_dir / "taskset.toml"
    if not path.exists():
        return []
    return list(tomllib.loads(path.read_text(encoding="utf-8")).get("tasks", []))


# ---------------------------------------------------------------- import pipeline


@dataclass
class Outcome:
    instance_id: str
    accepted: bool
    reason: str = ""
    image_gb: float | None = None  # compressed, on Docker Hub
    image_disk_gb: float | None = None  # unpacked, on this machine
    pull_s: float | None = None
    validate_s: float | None = None


def import_tasks(
    instances: Iterable[dict],
    tasks_dir: Path,
    rules: Rules,
    limit: int,
    version: str,
    size_of: Callable[[str], float | None],
    validate: Callable[[Path], tuple[bool, str]] | None,
    prepare: Callable[[str], tuple[float, float | None]] | None = None,
    cleanup: Callable[[str], None] | None = None,
    max_validate_s: float = 900,
    timeout_s: int = 1800,
    log: Callable[[str], None] = print,
) -> list[Outcome]:
    """Write up to `limit` tasks into `tasks_dir` (appending to a set already there), keeping only
    those that validate (base fails, gold patch passes) within `max_validate_s`.

    validate(task_dir) -> (ok, detail); prepare(image) -> (pull seconds, unpacked GB) pulls the image;
    cleanup(image) removes it. Pass validate=None to write tasks without checking them."""
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / ".gitattributes").write_text("# Patches are whitespace-exact.\n* -text\n", encoding="utf-8")
    kept = existing_ids(tasks_dir)
    rules.exclude |= set(kept)
    outcomes: list[Outcome] = []
    stats = _load_stats(tasks_dir)

    def reject(inst: dict, reason: str) -> None:
        outcomes.append(Outcome(inst["instance_id"], False, reason, inst.get("_image_gb")))

    per_repo: dict[str, int] = {}  # tasks already in the set count toward the per-repo cap
    for t in kept:
        repo = stats.get(t, {}).get("repo", t)
        per_repo[repo] = per_repo.get(repo, 0) + 1
    if len(kept) >= limit:
        return outcomes
    for inst in candidates(instances, rules, size_of, on_reject=reject, per_repo=per_repo):
        iid, image = inst["instance_id"], inst["docker_image"]
        out = Outcome(iid, False, image_gb=inst.get("_image_gb"))
        task_dir = write_task(inst, tasks_dir, timeout_s=timeout_s)
        if validate is not None:
            try:
                if prepare:
                    out.pull_s, out.image_disk_gb = prepare(image)
                t0 = time.monotonic()
                ok, detail = validate(task_dir)
                out.validate_s = round(time.monotonic() - t0, 1)
            except Exception as e:
                ok, detail = False, f"{type(e).__name__}: {e}"
            if ok and out.validate_s is not None and out.validate_s > max_validate_s:
                ok, detail = False, f"validation took {out.validate_s:.0f}s > {max_validate_s:.0f}s"
            if cleanup and not ok:
                cleanup(image)
            if not ok:
                out.reason = detail
                _rmtree(task_dir)
                per_repo[inst["repo"]] -= 1  # a rejected task doesn't use up its repo's quota
                outcomes.append(out)
                _save_rejections(tasks_dir, [out])  # saved as we go, so an interrupted run keeps them
                log(f"  reject {iid}: {detail[:600]}")
                continue
        out.accepted = True
        outcomes.append(out)
        kept.append(iid)
        stats[iid] = {k: v for k, v in vars(out).items() if k not in ("instance_id", "accepted", "reason")}
        stats[iid]["repo"] = inst["repo"]
        stats[iid]["created_at"] = str(inst.get("created_at", ""))[:10]
        write_taskset(tasks_dir, kept, version)
        _save_stats(tasks_dir, stats)
        log(f"  keep   {iid} ({len(kept)}/{limit}) image {_gb(out.image_gb)} compressed / "
            f"{_gb(out.image_disk_gb)} on disk, validated in {out.validate_s}s")
        if len(kept) >= limit:
            break
    write_taskset(tasks_dir, kept, version)
    _save_stats(tasks_dir, stats)
    _save_rejections(tasks_dir, [o for o in outcomes if not o.accepted])
    return outcomes


def _gb(v: float | None) -> str:
    return "?" if v is None else f"{v:.2f} GB"


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


STATS = "import-stats.json"


def _load_stats(tasks_dir: Path) -> dict[str, dict]:
    p = tasks_dir / STATS
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_rejections(tasks_dir: Path, rejected: list[Outcome]) -> None:
    """Why each instance looked at was left out (merged with earlier runs)."""
    p = tasks_dir / "import-rejected.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    old.update({o.instance_id: o.reason.splitlines()[0][:600] if o.reason else "" for o in rejected})
    p.write_text(json.dumps(old, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _save_stats(tasks_dir: Path, stats: dict[str, dict]) -> None:
    (tasks_dir / STATS).write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n", encoding="utf-8")
