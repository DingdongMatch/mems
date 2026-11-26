#!/usr/bin/env python3
"""L3 archive reader using only the Python standard library.

Usage:
    python reader.py <agent_id> [storage_path]
    python reader.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_PATH = PROJECT_ROOT / "storage" / "l3_archive"


def read_records(filepath: str | Path) -> list[dict[str, Any]]:
    """Read valid JSON-object records, warning about malformed lines."""
    records: list[dict[str, Any]] = []
    with Path(filepath).open(encoding="utf-8") as archive:
        for line_num, line in enumerate(archive, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.warn(
                    f"Invalid JSON at line {line_num}: {exc}",
                    stacklevel=2,
                )
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                warnings.warn(
                    f"Ignoring non-object JSON at line {line_num}",
                    stacklevel=2,
                )
    return records


def record_matches_agent(record: dict[str, Any], agent_id: str) -> bool:
    """Return whether a JSONL record belongs to the requested agent."""
    return record.get("agent_id") == agent_id


def is_agent_archive(filepath: Path, agent_id: str) -> bool:
    """Recognize hashed archive names and scan JSONL identity as a fallback."""
    agent_suffix = hashlib.sha256(agent_id.encode()).hexdigest()[:12]
    if filepath.name.startswith(f"l3_{agent_suffix}_"):
        return True
    # Legacy archives used the raw agent id in their filename.
    if filepath.name.startswith(f"l3_{agent_id}_"):
        return True
    return any(record_matches_agent(record, agent_id) for record in read_records(filepath))


class ArchiveReader:
    """Read and search L3 archives without third-party dependencies."""

    def __init__(self, storage_path: str | Path = DEFAULT_STORAGE_PATH) -> None:
        self.storage_path = Path(storage_path)

    def list_archives(self, agent_id: str) -> list[dict[str, Any]]:
        """List archives for an agent using current or legacy naming."""
        if not self.storage_path.exists():
            return []

        archives = []
        for filepath in sorted(self.storage_path.glob("*.jsonl")):
            if not filepath.is_file() or not is_agent_archive(filepath, agent_id):
                continue
            stat = filepath.stat()
            archives.append(
                {
                    "filename": filepath.name,
                    "filepath": str(filepath),
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return archives

    def read_archive(self, filepath: str | Path) -> list[dict[str, Any]]:
        """Read one archive, returning an empty list when it does not exist."""
        path = Path(filepath)
        if not path.exists():
            warnings.warn(f"File not found: {path}", stacklevel=2)
            return []
        return read_records(path)

    def search_archive(
        self,
        agent_id: str,
        keyword: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search content and creation dates across an agent's archives."""
        results = []
        for archive in self.list_archives(agent_id):
            for record in self.read_archive(archive["filepath"]):
                if keyword and keyword not in record.get("content", ""):
                    continue
                created_at = record.get("created_at", "")
                if start_date and created_at < start_date:
                    continue
                if end_date and created_at > end_date:
                    continue
                results.append(record)
        return results

    def get_statistics(self, agent_id: str) -> dict[str, Any]:
        """Return archive counts and sizes for an agent."""
        archives = self.list_archives(agent_id)
        total_size = sum(archive["size_bytes"] for archive in archives)
        return {
            "agent_id": agent_id,
            "total_archives": len(archives),
            "total_records": sum(
                len(self.read_archive(archive["filepath"])) for archive in archives
            ),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_id", nargs="?")
    parser.add_argument(
        "storage_path",
        nargs="?",
        type=Path,
        default=Path(os.getenv("ARCHIVE_STORAGE_PATH", str(DEFAULT_STORAGE_PATH))),
    )
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--search")
    args = parser.parse_args()
    if not args.agent_id:
        parser.print_help()
        return
    if not args.storage_path.is_absolute():
        args.storage_path = PROJECT_ROOT / args.storage_path

    reader = ArchiveReader(args.storage_path)
    if args.stats:
        stats = reader.get_statistics(args.agent_id)
        print(f"\n=== Archive Statistics for {args.agent_id} ===")
        print(f"Total archives: {stats['total_archives']}")
        print(f"Total records: {stats['total_records']}")
        print(f"Total size: {stats['total_size_mb']} MB")
        return

    if args.search:
        results = reader.search_archive(args.agent_id, keyword=args.search)
        print(f"\n=== Search Results for '{args.search}' ===")
        print(f"Found {len(results)} records:\n")
        for record in results[:10]:
            print(
                f"- {record.get('created_at', 'unknown')}: "
                f"{record.get('content', '')[:100]}..."
            )
        if len(results) > 10:
            print(f"... and {len(results) - 10} more")
        return

    archives = reader.list_archives(args.agent_id)
    print(f"\n=== Archives for {args.agent_id} ===")
    if not archives:
        print("No archives found.")
        return
    for archive in archives:
        print(
            f"- {archive['filename']} "
            f"({archive['size_bytes']} bytes, {archive['modified']})"
        )


if __name__ == "__main__":
    main()
