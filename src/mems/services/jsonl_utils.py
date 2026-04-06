import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class JsonlWriter:
    """JSONL 文件写入工具"""

    def __init__(self, dir_path: Path, prefix: str):
        """Prepare a JSONL writer rooted at the target directory.

        初始化指向目标目录的 JSONL 写入器。
        """
        self.dir_path = Path(dir_path)
        self.prefix = prefix
        self.dir_path.mkdir(parents=True, exist_ok=True)

    def _get_filename(self, agent_id: str, date: Optional[datetime] = None) -> str:
        """Build the daily JSONL filename for an agent.

        为指定 agent 生成按天切分的 JSONL 文件名。
        """
        date = date or datetime.now(timezone.utc)
        return f"{self.prefix}_{agent_id}_{date.strftime('%Y%m%d')}.jsonl"

    def write(
        self, agent_id: str, data: Dict[str, Any], date: Optional[datetime] = None
    ) -> Path:
        """Append a single JSON record to the agent file.

        向 agent 对应文件追加一条 JSON 记录。
        """
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
        """Append multiple JSON records to the same agent file.

        向同一个 agent 文件追加多条 JSON 记录。
        """
        filename = self._get_filename(agent_id, date)
        filepath = self.dir_path / filename
        with open(filepath, "a", encoding="utf-8") as f:
            for data in data_list:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return filepath


class JsonlReader:
    """JSONL 文件读取工具"""

    def __init__(self, dir_path: Path):
        """Prepare a JSONL reader rooted at the target directory.

        初始化指向目标目录的 JSONL 读取器。
        """
        self.dir_path = Path(dir_path)

    def read(self, filepath: Path) -> List[Dict[str, Any]]:
        """Read all JSON objects from a single JSONL file.

        读取单个 JSONL 文件中的全部 JSON 对象。
        """
        results = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        return results

    def read_by_agent(self, agent_id: str, prefix: str = "*") -> List[Dict[str, Any]]:
        """Read all JSONL records for an agent by filename pattern.

        按文件名模式读取某个 agent 的全部 JSONL 记录。
        """
        results = []
        pattern = f"{prefix}_{agent_id}_*.jsonl"
        for filepath in self.dir_path.glob(pattern):
            results.extend(self.read(filepath))
        return results

    def read_date_range(
        self,
        agent_id: str,
        start_date: datetime,
        end_date: datetime,
        prefix: str = "*",
    ) -> List[Dict[str, Any]]:
        """Read agent JSONL records across an inclusive date range.

        读取某个 agent 在给定日期区间内的 JSONL 记录。
        """
        results = []
        current = start_date
        while current <= end_date:
            pattern = f"{prefix}_{agent_id}_{current.strftime('%Y%m%d')}.jsonl"
            for filepath in self.dir_path.glob(pattern):
                results.extend(self.read(filepath))
            current = current + timedelta(days=1)
        return results
