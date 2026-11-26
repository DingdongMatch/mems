from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import mems_benchmark_ragas as bench
import pytest


def test_parse_metric_names():
    assert bench.parse_metric_names("faithfulness,answer_relevance") == [
        "faithfulness",
        "answer_relevance",
    ]
    with pytest.raises(SystemExit):
        bench.parse_metric_names("")
    with pytest.raises(SystemExit):
        bench.parse_metric_names("not_a_metric")


def test_percentile():
    assert bench.percentile([], 0.5) == 0.0
    assert bench.percentile([5.0], 0.5) == 5.0
    values = [1.0, 2.0, 3.0, 4.0]
    assert bench.percentile(values, 0.5) == 2.5
    assert bench.percentile(values, 0.0) == 1.0
    assert bench.percentile(values, 1.0) == 4.0


def test_build_ragas_dataset_filters_non_eligible(monkeypatch):
    rows = [
        {
            "case_id": "ok_1",
            "status": "ok",
            "ragas_eligible": True,
            "question": "q1",
            "answer": "a1",
            "contexts": ["c1"],
            "ground_truth": "g1",
        },
        {
            "case_id": "hygiene_1",
            "status": "ok",
            "ragas_eligible": False,
            "question": "q2",
            "answer": "a2",
            "contexts": ["c2"],
            "ground_truth": "g2",
        },
        {
            "case_id": "failed_1",
            "status": "failed",
            "ragas_eligible": True,
            "question": "q3",
            "answer": "",
            "contexts": [],
            "ground_truth": "g3",
        },
    ]
    valid_rows, indexes = bench.eligible_ragas_rows(rows)
    assert indexes == [0]
    assert valid_rows == [rows[0]]

    class FakeDataset:
        def __init__(self, values):
            self.values = values

        def __len__(self):
            return len(self.values["user_input"])

        def __getitem__(self, key):
            return self.values[key]

    fake_module = SimpleNamespace(
        Dataset=SimpleNamespace(from_dict=lambda values: FakeDataset(values))
    )
    monkeypatch.setitem(sys.modules, "datasets", fake_module)
    dataset, indexes = bench.build_ragas_dataset(rows)
    assert indexes == [0]
    assert len(dataset) == 1
    assert dataset["user_input"][0] == "q1"


def test_aggregate_results_mean_std():
    base = {
        "case_id": "case_a",
        "scenario_type": "cross_layer_trace",
        "language": "en",
        "question": "q",
        "ground_truth": "g",
        "ragas_eligible": True,
        "status": "ok",
        "notes": "",
        "answer": "a",
        "contexts": ["c"],
        "retrieved_sources": ["l1_episodic"],
    }
    rows = [
        {
            **base,
            "trial": 1,
            "query_ms": 100.0,
            "end_to_end_ms": 150.0,
            "faithfulness": 0.8,
        },
        {
            **base,
            "trial": 2,
            "query_ms": 200.0,
            "end_to_end_ms": 250.0,
            "faithfulness": 1.0,
        },
    ]
    aggregated = bench.aggregate_results(rows)
    assert len(aggregated) == 1
    row = aggregated[0]
    assert row["trial_count"] == 2
    assert row["status"] == "ok"
    assert row["query_ms"] == 150.0
    assert math.isclose(row["query_ms_std"], 70.710678, rel_tol=1e-3)
    assert row["faithfulness"] == 0.9
    assert "trial" not in row


def test_aggregate_results_failed_status_wins():
    rows = [
        {
            "case_id": "case_b",
            "scenario_type": "l0_recall",
            "language": "en",
            "question": "q",
            "ground_truth": "g",
            "ragas_eligible": False,
            "trial": 1,
            "status": "ok",
            "notes": "l0_hit",
            "answer": "",
            "contexts": [],
            "retrieved_sources": [],
            "query_ms": 10.0,
            "end_to_end_ms": 20.0,
        },
        {
            "case_id": "case_b",
            "scenario_type": "l0_recall",
            "language": "en",
            "question": "q",
            "ground_truth": "g",
            "ragas_eligible": False,
            "trial": 2,
            "status": "failed",
            "notes": "pipeline_error: RuntimeError: boom",
            "error_type": "RuntimeError",
            "error_message": "boom",
            "error_traceback": "tb",
            "answer": "",
            "contexts": [],
            "retrieved_sources": [],
            "query_ms": 0.0,
            "end_to_end_ms": 30.0,
        },
    ]
    aggregated = bench.aggregate_results(rows)
    assert aggregated[0]["status"] == "failed"
    assert "pipeline_error" in aggregated[0]["notes"]


def test_hint_metrics():
    contexts = ["The user likes Rust.", "User prefers Python."]
    assert bench._hint_metrics(contexts, "Rust") == (1, 1)
    assert bench._hint_metrics(contexts, "Python") == (1, 2)
    assert bench._hint_metrics(contexts, "missing") == (0, 0)
    assert bench._hint_metrics(contexts, "") == (0, 0)


def test_aggregate_results_includes_retrieval_fields():
    base = {
        "case_id": "case_r",
        "scenario_type": "recency_conflict",
        "language": "en",
        "question": "q",
        "ground_truth": "g",
        "ragas_eligible": True,
        "status": "ok",
        "notes": "",
        "answer": "a",
        "contexts": ["new fact", "old fact"],
        "retrieved_sources": ["l1_episodic"],
    }
    rows = [
        {
            **base,
            "trial": 1,
            "l1_hit": 1,
            "l1_rr": 1.0,
            "l2_hit": 1,
            "l2_rr": 0.5,
            "conflict_resolved": 1,
            "end_to_end_ms": 10.0,
        },
        {
            **base,
            "trial": 2,
            "l1_hit": 0,
            "l1_rr": 0.0,
            "l2_hit": 1,
            "l2_rr": 1.0,
            "conflict_resolved": 0,
            "end_to_end_ms": 20.0,
        },
    ]
    aggregated = bench.aggregate_results(rows)
    row = aggregated[0]
    assert row["l1_hit"] == 0.5
    assert row["l1_rr"] == 0.5
    assert row["l2_hit"] == 1.0
    assert row["conflict_resolved"] == 0.5


def test_report_conflict_resolved_only_counts_conflict_scenarios(tmp_path):
    """recency_conflict_resolved must ignore non-conflict cases (which are 1)."""

    def make_row(case_id: str, scenario: str, conflict: int) -> dict:
        return {
            "case_id": case_id,
            "scenario_type": scenario,
            "language": "en",
            "question": "q",
            "ground_truth": "g",
            "trial": 1,
            "ragas_eligible": True,
            "status": "ok",
            "notes": "",
            "answer": "a",
            "contexts": ["c"],
            "retrieved_sources": ["l1_episodic"],
            "l1_hit": 1,
            "l2_hit": 1,
            "l1_rr": 1.0,
            "l2_rr": 1.0,
            "conflict_resolved": conflict,
            "write_ms": 1.0,
            "distill_ms": 1.0,
            "query_ms": 1.0,
            "answer_ms": 1.0,
            "end_to_end_ms": 5.0,
        }

    rows = [
        make_row("rc_1", "recency_conflict", 0),
        make_row("rc_2", "recency_conflict", 0),
        make_row("cross_1", "cross_layer_trace", 1),
    ]
    report = tmp_path / "report.md"
    bench.build_report(
        rows,
        report,
        "http://x",
        8,
        bench.EvaluatorConfig(
            api_key="test-key",
            base_url="http://x",
            model="test-model",
            embedding_model="test-embedding",
            embedding_dimensions=None,
            timeout=30,
        ),
        bench.EvaluatorConfig(
            api_key="test-key",
            base_url="http://x",
            model="test-model",
            embedding_model="test-embedding",
            embedding_dimensions=None,
            timeout=30,
        ),
    )
    text = report.read_text(encoding="utf-8")
    line = next(
        line for line in text.splitlines() if "recency_conflict_resolved:" in line
    )
    assert "0.0000" in line


def test_ragas_config_falls_back_to_settings(monkeypatch):
    for key in (
        "RAGAS_OPENAI_API_KEY",
        "RAGAS_OPENAI_BASE_URL",
        "RAGAS_OPENAI_MODEL",
        "RAGAS_OPENAI_EMBEDDING_MODEL",
        "RAGAS_OPENAI_EMBEDDING_DIMENSIONS",
        "RAGAS_OPENAI_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    fake_settings = SimpleNamespace(
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="http://llm.test/v1",
        OPENAI_MODEL="test-model",
        OPENAI_EMBEDDING_MODEL="test-embedding",
        OPENAI_TIMEOUT=30,
    )
    monkeypatch.setattr(bench, "_settings", lambda: fake_settings)
    config = bench.get_ragas_evaluator_config()
    assert config.api_key == "test-key"
    assert config.base_url == "http://llm.test/v1"
    assert config.model == "test-model"
    assert config.embedding_dimensions is None


def test_no_hardcoded_api_key_in_source():
    source = Path(__file__).with_name("mems_benchmark_ragas.py").read_text(
        encoding="utf-8"
    )
    assert "sk-" not in source


def test_output_paths_are_repository_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert bench.resolve_output_path("benchmark_reports/results.json") == (
        bench.PROJECT_ROOT / "benchmark_reports" / "results.json"
    )
    absolute = tmp_path / "custom.json"
    assert bench.resolve_output_path(absolute) == absolute


def test_import_has_no_optional_or_production_side_effects(tmp_path):
    """Collection must work without RAGAS/OpenAI or a configured database."""
    tests_dir = Path(__file__).resolve().parent
    env = {**os.environ, "PYTHONPATH": str(tests_dir)}
    code = """
import builtins
import sys

forbidden = (
    "datasets", "ragas", "openai", "mems.config",
    "mems.infrastructure.persistence.database",
    "mems.interfaces.bootstrap",
)
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if any(name == item or name.startswith(item + ".") for item in forbidden):
        raise AssertionError(f"eager import: {name}")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import mems_benchmark_ragas
assert not any(
    name == item or name.startswith(item + ".")
    for name in sys.modules
    for item in forbidden
)
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
