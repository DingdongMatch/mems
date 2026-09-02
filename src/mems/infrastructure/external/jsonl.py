"""JSONL file read/write utilities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class JsonlWriter:
    """JSONL 文件写入工具"""

    def __init__(self, dir_path: Path, prefix: str):
        self.dir_path = Path(dir_path)
        self.prefix = prefix
        self.dir_path.mkdir(parents=True, exist_ok=True)

    def _get_filename(self, agent_id: str, date: Optional[datetime] = None) -> str:
        date = date or datetime.now(timezone.utc)
        return f"{self.prefix}_{agent_id}_{date.strftime('%Y%m%d')}.jsonl"

    def write(
        self, agent_id: str, data: Dict[str, Any], date: Optional[datetime] = None
    ) -> Path:
        filename = self._get_filename(agent_id, date)
        filepath = self.dir_path / filename
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return filepath

    def write_batch(
        self,
        agent_id: str,
        data_list: List[Dict[str, Any]],
        date: Optional[datetime] = None,
    ) -> Path:
        filename = self._get_filename(agent_id, date)
        filepath = self.dir_path / filename
        with open(filepath, "a", encoding="utf-8") as f:
            for data in data_list:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return filepath


class JsonlReader:
    """JSONL 文件读取工具"""

    def __init__(self, dir_path: Path):
        self.dir_path = Path(dir_path)

    def read(self, filepath: Path) -> List[Dict[str, Any]]:
        results = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        return results
