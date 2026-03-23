#!/usr/bin/env python3
"""
L3 归档读取器 - 纯 Python 标准库实现
不依赖任何第三方框架，用于百年级数据读取

用法:
    python reader.py <agent_id> [storage_path]
    python reader.py --help
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional


class ArchiveReader:
    """L3 归档读取器 - 纯标准库实现"""

    def __init__(self, storage_path: str = "storage/l3_archive"):
        self.storage_path = Path(storage_path)

    def list_archives(self, agent_id: str) -> List[Dict[str, Any]]:
        """列出某个 Agent 的所有归档文件"""
        if not self.storage_path.exists():
            return []

        archives = []
        pattern = f"l3_{agent_id}_*.jsonl"

        for filepath in sorted(self.storage_path.glob(pattern)):
            stat = filepath.stat()
            archives.append({
                "filename": filepath.name,
                "filepath": str(filepath),
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })

        return archives

    def read_archive(self, filepath: str) -> List[Dict[str, Any]]:
        """读取单个归档文件"""
        records = []
        filepath = Path(filepath)

        if not filepath.exists():
            print(f"Error: File not found: {filepath}")
            return records

        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON at line {line_num}: {e}")

        return records

    def search_archive(
        self,
        agent_id: str,
        keyword: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """在归档中搜索内容"""
        results = []
        archives = self.list_archives(agent_id)

        for archive in archives:
            records = self.read_archive(archive["filepath"])

            for record in records:
                if keyword and keyword not in record.get("content", ""):
                    continue

                created_at = record.get("created_at", "")
                if start_date and created_at < start_date:
                    continue
                if end_date and created_at > end_date:
                    continue

                results.append(record)

        return results

    def get_statistics(self, agent_id: str) -> Dict[str, Any]:
        """获取归档统计信息"""
        archives = self.list_archives(agent_id)
        total_records = 0
        total_size = 0

        for archive in archives:
            records = self.read_archive(archive["filepath"])
            total_records += len(records)
            total_size += archive["size_bytes"]

        return {
            "agent_id": agent_id,
            "total_archives": len(archives),
            "total_records": total_records,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
        }


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--help":
        print(__doc__)
        print("Examples:")
        print("  python reader.py agent_001")
        print("  python reader.py agent_001 storage/l3_archive")
        print("  python reader.py agent_001 --stats")
        print("  python reader.py agent_001 --search \"关键词\"")
        return

    agent_id = sys.argv[1]
    storage_path = "storage/l3_archive"
    if len(sys.argv) > 2 and not sys.argv[2].startswith("--"):
        storage_path = sys.argv[2]

    reader = ArchiveReader(storage_path)

    if "--stats" in sys.argv:
        stats = reader.get_statistics(agent_id)
        print(f"\n=== Archive Statistics for {agent_id} ===")
        print(f"Total archives: {stats['total_archives']}")
        print(f"Total records: {stats['total_records']}")
        print(f"Total size: {stats['total_size_mb']} MB")
        return

    search_keyword = None
    if "--search" in sys.argv:
        try:
            idx = sys.argv.index("--search")
            if idx + 1 < len(sys.argv):
                search_keyword = sys.argv[idx + 1]
        except ValueError:
            pass

    if search_keyword:
        results = reader.search_archive(agent_id, keyword=search_keyword)
        print(f"\n=== Search Results for '{search_keyword}' ===")
        print(f"Found {len(results)} records:\n")
        for r in results[:10]:
            print(f"- {r.get('created_at', 'unknown')}: {r.get('content', '')[:100]}...")
        if len(results) > 10:
            print(f"... and {len(results) - 10} more")
        return

    archives = reader.list_archives(agent_id)
    print(f"\n=== Archives for {agent_id} ===")
    if not archives:
        print("No archives found.")
        return

    for a in archives:
        print(f"- {a['filename']} ({a['size_bytes']} bytes, {a['modified']})")


if __name__ == "__main__":
    main()