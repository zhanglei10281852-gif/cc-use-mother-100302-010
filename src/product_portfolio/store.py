"""持久化：内存存储与 JSON 文件存储（原子写入，无外部依赖）。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "products": {},
        "config_versions": {},
        "opportunities": {},
        "records": {},
        "reviews": {},
        "counters": {"review": 0},
    }


class InMemoryStore:
    """测试与演示用的内存存储。"""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state = state if state is not None else empty_state()

    def load(self) -> dict[str, Any]:
        return copy.deepcopy(self._state)

    def save(self, state: dict[str, Any]) -> None:
        self._state = copy.deepcopy(state)


class JsonFileStore:
    """单文件 JSON 存储；写入采用临时文件 + 原子替换，避免半截文件。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_state()
        with self.path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
        if state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"存储文件 schema_version={state.get('schema_version')!r} 与当前版本 {SCHEMA_VERSION} 不兼容"
            )
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.path)
