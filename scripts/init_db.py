#!/usr/bin/env python3
"""
数据库初始化脚本
创建所有表结构
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mems.database import init_db
from mems.config import settings


def main():
    print("Initializing database...")
    init_db()
    print(f"Database initialized: {settings.DATABASE_URL}")

    Path(settings.storage_l3_path).mkdir(parents=True, exist_ok=True)
    print("Storage directories created.")


if __name__ == "__main__":
    main()
