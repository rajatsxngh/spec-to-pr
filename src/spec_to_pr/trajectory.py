"""Trajectory logging (F3): every step to JSONL with tool call, output, tokens and cost."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _jsonable(block: Any) -> Any:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    return str(block)


class Trajectory:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a", encoding="utf-8")

    def write(self, event: str, **fields: Any) -> None:
        record = {"event": event, "ts": round(time.time(), 3), **fields}
        self._f.write(json.dumps(record, default=_jsonable, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()

    @staticmethod
    def read(path: Path) -> list[dict[str, Any]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
