from __future__ import annotations

from mems.domain.services.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT,
    RECONCILE_SYSTEM_PROMPT,
    RECONCILE_USER_PROMPT,
)


def test_extraction_is_context_free():
    """Extraction must not see existing L2 context or produce conflicts."""
    assert "Existing L2 Context" not in EXTRACTION_USER_PROMPT
    assert "conflict_candidates" not in EXTRACTION_SYSTEM_PROMPT
    assert "__CONTENT__" in EXTRACTION_USER_PROMPT


def test_extraction_guards_against_hallucination():
    """Extraction prompt requires verbatim evidence and no invented facts."""
    assert "不得臆造" in EXTRACTION_SYSTEM_PROMPT
    assert "逐字" in EXTRACTION_SYSTEM_PROMPT
    assert "confidence" in EXTRACTION_SYSTEM_PROMPT


def test_reconcile_handles_multi_value_and_complete_output():
    """Reconcile must allow multi-value predicates and output full JSON."""
    assert "多值谓词" in RECONCILE_SYSTEM_PROMPT
    assert "输出完整 JSON" in RECONCILE_SYSTEM_PROMPT
    assert "conflict_candidates" in RECONCILE_SYSTEM_PROMPT


def test_reconcile_user_has_markers():
    assert "__CANDIDATES__" in RECONCILE_USER_PROMPT
    assert "__EXISTING_CONTEXT__" in RECONCILE_USER_PROMPT
