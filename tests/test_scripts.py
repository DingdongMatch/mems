from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from mems.interfaces.api.schemas import (
    MemsQueryRequest,
    MemsWriteRequest,
    RedistillRequest,
)
from scripts import demo
from scripts.reader import ArchiveReader, is_agent_archive, read_records


def _write_archive(path: Path, *records: dict) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_demo_uses_current_identity_and_namespace_schema(monkeypatch, capsys):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/mems/write":
            MemsWriteRequest.model_validate_json(request.content)
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/v1/mems/query":
            MemsQueryRequest.model_validate_json(request.content)
            return httpx.Response(
                200,
                json={
                    "query": "What programming language do I like?",
                    "results": [
                        {
                            "source": "l0_hot",
                            "content": "I like Rust.",
                            "score": 1.0,
                        }
                    ],
                    "total": 1,
                },
            )
        if request.url.path == "/v1/mems/status":
            return httpx.Response(200, json={"status": "healthy", "pipeline": {}})
        if request.url.path == "/v1/mems/redistill":
            RedistillRequest.model_validate_json(request.content)
            payload = json.loads(request.content)
            assert payload == {
                "tenant_id": "demo-tenant",
                "user_id": "demo-user",
                "agent_id": "demo-agent",
                "namespace": ["demos", "quick-start"],
                "min_importance": 0.0,
            }
            return httpx.Response(200, json={"success": True, "processed": 0})
        raise AssertionError(f"Unexpected request: {request.url}")

    original_client = httpx.Client
    monkeypatch.setattr(
        demo.httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(sys, "argv", ["demo.py", "--base-url", "http://mems.test"])

    demo.main()

    payloads = [json.loads(request.content) for request in requests[:2]]
    assert "scope" not in payloads[0]
    assert "scope" not in payloads[1]
    for field in ("tenant_id", "user_id", "agent_id", "namespace"):
        assert payloads[0][field] == payloads[1][field]
    assert "I like Rust." in capsys.readouterr().out


def test_reader_discovers_hashed_legacy_and_record_scanned_archives(tmp_path):
    agent_id = "agent_001"
    suffix = hashlib.sha256(agent_id.encode()).hexdigest()[:12]
    hashed = tmp_path / f"l3_{suffix}_default_2026_09_batch.jsonl"
    legacy = tmp_path / "l3_agent_001_2026_08_batch.jsonl"
    scanned = tmp_path / "renamed_archive.jsonl"
    unrelated = tmp_path / "l3_other_2026_09_batch.jsonl"
    _write_archive(hashed, {"agent_id": agent_id, "content": "hashed"})
    _write_archive(legacy, {"agent_id": agent_id, "content": "legacy"})
    _write_archive(scanned, {"agent_id": agent_id, "content": "scanned"})
    _write_archive(unrelated, {"agent_id": "another-agent", "content": "private"})

    reader = ArchiveReader(tmp_path)
    archives = reader.list_archives(agent_id)

    assert [Path(item["filepath"]) for item in archives] == [
        hashed,
        legacy,
        scanned,
    ]
    assert is_agent_archive(hashed, agent_id)
    assert not is_agent_archive(unrelated, agent_id)
    assert reader.list_archives("missing-agent") == []


def test_reader_search_statistics_and_malformed_lines(tmp_path):
    archive = tmp_path / "archive.jsonl"
    archive.write_text(
        json.dumps(
            {
                "agent_id": "agent_001",
                "content": "I like Rust",
                "created_at": "2026-09-01T10:00:00+00:00",
            }
        )
        + "\n{broken\n[]\n"
        + json.dumps(
            {
                "agent_id": "agent_001",
                "content": "I prefer concise answers",
                "created_at": "2026-09-02T10:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reader = ArchiveReader(tmp_path)

    with pytest.warns(UserWarning) as caught:
        records = read_records(archive)
    assert any("Invalid JSON" in str(item.message) for item in caught)
    assert any("non-object" in str(item.message) for item in caught)
    assert len(records) == 2
    with pytest.warns(UserWarning):
        assert len(reader.read_archive(archive)) == 2
    with pytest.warns(UserWarning):
        results = reader.search_archive(
            "agent_001",
            keyword="Rust",
            start_date="2026-09-01",
            end_date="2026-09-01T23:59:59+00:00",
        )
    assert len(results) == 1
    with pytest.warns(UserWarning):
        stats = reader.get_statistics("agent_001")
    assert stats["total_archives"] == 1
    assert stats["total_records"] == 2
    assert stats["total_size_bytes"] == archive.stat().st_size


def test_reader_missing_storage_and_file(tmp_path):
    reader = ArchiveReader(tmp_path / "missing")
    assert reader.list_archives("agent_001") == []
    with pytest.warns(UserWarning, match="File not found"):
        assert reader.read_archive(tmp_path / "missing.jsonl") == []
