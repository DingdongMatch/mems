"""JSONL file read/write utilities."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


logger = logging.getLogger(__name__)


class JsonlWriter:
    """JSONL 文件写入工具"""

    def __init__(self, dir_path: Path, prefix: str):
        self.dir_path = Path(dir_path)
        self.prefix = prefix
        self.dir_path.mkdir(parents=True, exist_ok=True)

    def _get_filename(self, agent_id: str, date: Optional[datetime] = None) -> str:
        date = date or datetime.now(timezone.utc)
        return f"{self.prefix}_{agent_id}_{date.strftime('%Y%m%d')}.jsonl"

    def write(self, agent_id: str, data: Dict[str, Any], date: Optional[datetime] = None) -> Path:
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

    def iter_records(self, filepath: Path) -> Iterator[tuple[int, Dict[str, Any]]]:
        """Yield valid objects with their physical, one-based line numbers."""
        with open(filepath, "rb") as f:
            for line_no, raw_line in enumerate(f, start=1):
                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    logger.warning(
                        "Skipping malformed JSONL record",
                        extra={"l3_path": str(filepath), "l3_line": line_no},
                    )
                    continue
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (TypeError, ValueError, RecursionError):
                    logger.warning(
                        "Skipping malformed JSONL record",
                        extra={"l3_path": str(filepath), "l3_line": line_no},
                    )
                    continue
                if not isinstance(record, dict):
                    logger.warning(
                        "Skipping non-object JSONL record",
                        extra={"l3_path": str(filepath), "l3_line": line_no},
                    )
                    continue
                yield line_no, record

    def read(self, filepath: Path) -> List[Dict[str, Any]]:
        """Compatibility wrapper returning records without line numbers."""
        return [record for _line_no, record in self.iter_records(filepath)]
