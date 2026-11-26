"""Distillation policy helpers.

蒸馏策略：高价值判断、低价值过滤与批内排序规则。
"""

from __future__ import annotations

import re
from typing import Optional

from mems.domain.identity import Importance
from mems.domain.memory import EpisodicMemory


MIN_DISTILL_IMPORTANCE = 0.5
_MIN_ASCII_LENGTH = 8
_MIN_CJK_LENGTH = 2
_ASCII_NOISE = re.compile(r"\b(?:thanks|thank\s+you|ok|okay)\b", re.IGNORECASE)
_CJK_NOISE = ("你好", "谢谢", "收到", "好的")


def is_high_value(importance_score: float, threshold: float) -> bool:
    """Return True when a score meets the high-importance threshold."""
    return Importance(value=importance_score).is_high_value(threshold)


def eligible_for_distill(
    memory: EpisodicMemory,
    force: bool,
    min_importance: float,
) -> bool:
    """Return True when an episodic record is eligible for distillation."""
    if memory.importance_score < min_importance:
        return False
    if force:
        return True
    return not memory.is_distilled


def filter_record(content: str) -> Optional[str]:
    """Skip low-value source records before sending them to the LLM.

    在调用 LLM 之前过滤低价值的来源记录，返回跳过原因。
    """
    content = content.strip()
    lowered = content.lower()

    if not content:
        return "empty"
    cjk_length = len(re.findall(r"[\u4e00-\u9fff]", content))
    minimum_length = _MIN_CJK_LENGTH if cjk_length else _MIN_ASCII_LENGTH
    measured_length = cjk_length if cjk_length else len(content)
    if measured_length < minimum_length:
        return "low_signal"
    if (
        all(marker in lowered for marker in ["user:", "assistant:"])
        and len(content) < 30
    ):
        return "low_signal"
    if (_ASCII_NOISE.search(lowered) or any(m in lowered for m in _CJK_NOISE)) and len(
        content
    ) < 40:
        return "greeting"
    return None
