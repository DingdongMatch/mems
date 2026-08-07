"""Minimal Mems demo: write a memory, recall it, inspect status.

最小化 Mems 演示：写入一条记忆 → 召回 → 查看状态 → 触发再蒸馏（无归档时为 no-op）。

先启动服务（uv run python -m mems.main），再运行本脚本：
    uv run python scripts/demo.py
"""

import argparse
import json

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Mems quick demo")
    parser.add_argument("--base-url", default="http://127.0.0.1:8210")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(base_url=base, timeout=30.0) as client:
        print("== 1. write ==")
        response = client.post(
            "/v1/mems/write",
            json={
                "agent_id": "demo-agent",
                "user_id": "demo-user",
                "scope": "private",
                "importance_score": 0.9,
                "messages": [
                    {
                        "role": "user",
                        "content": "I like Rust and prefer concise answers.",
                    }
                ],
            },
        )
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))

        print("\n== 2. query ==")
        response = client.post(
            "/v1/mems/query",
            json={
                "agent_id": "demo-agent",
                "user_id": "demo-user",
                "scope": "private",
                "query": "What programming language do I like?",
                "top_k": 5,
            },
        )
        response.raise_for_status()
        query = response.json()
        print(f"total results: {query['total']}")
        for item in query["results"]:
            print(f"  [{item['source']}] {item['content']}")

        print("\n== 3. status ==")
        response = client.get("/v1/mems/status")
        response.raise_for_status()
        status = response.json()
        print(f"status: {status['status']}")
        print(f"pipeline: {status['pipeline']}")

        print("\n== 4. redistill (no-op without archived records) ==")
        response = client.post("/v1/mems/redistill", json={"min_importance": 0.0})
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
