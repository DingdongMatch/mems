from __future__ import annotations

import math

import pytest

import mems_benchmark_ragas as bench


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


def test_build_ragas_dataset_filters_non_eligible():
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
        bench.get_answer_llm_config(),
        bench.get_ragas_evaluator_config(),
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
    config = bench.get_ragas_evaluator_config()
    assert config.api_key == bench.settings.OPENAI_API_KEY
    assert config.base_url == bench.settings.OPENAI_BASE_URL
    assert config.model == bench.settings.OPENAI_MODEL
    assert config.embedding_dimensions is None


def test_no_hardcoded_api_key_in_source():
    source = open("tests/mems_benchmark_ragas.py", encoding="utf-8").read()
    assert "sk-" not in source
