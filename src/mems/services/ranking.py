"""Pure ranking and lexical scoring helpers for hybrid memory recall.

混合召回使用的纯排序与词法打分辅助函数（不依赖 FastAPI / SQL，便于单测）。
"""

import re
from datetime import datetime, timezone

from mems.schemas import QueryResultItem


INTENT_KEYWORDS = {
    "profile": {
        "like",
        "likes",
        "dislike",
        "dislikes",
        "prefer",
        "preference",
        "habit",
        "style",
        "喜欢",
        "讨厌",
        "偏好",
        "习惯",
        "风格",
    },
    "fact": {
        "who",
        "what",
        "where",
        "project",
        "language",
        "relationship",
        "事实",
        "项目",
        "技术",
        "关系",
        "语言",
    },
    "event": {
        "recent",
        "today",
        "yesterday",
        "doing",
        "working",
        "latest",
        "最近",
        "今天",
        "昨天",
        "正在",
        "进展",
    },
    "summary": {
        "summary",
        "overview",
        "focus",
        "trend",
        "总结",
        "概括",
        "关注",
        "趋势",
    },
}

SOURCE_INTENT = {
    "l1_episodic": "event",
    "l2_profile": "profile",
    "l2_fact": "fact",
    "l2_event": "event",
    "l2_summary": "summary",
}


def tokenize_terms(text: str) -> set[str]:
    """Split text into lexical terms: ASCII words plus CJK character bigrams.

    将文本切分为词法单元：ASCII 整词 + 中文相邻双字（bigram），
    避免无空格中文在 `\\W+` 切分下退化成整串单 token。
    """
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9_]+", lowered))
    cjk = "".join(ch for ch in lowered if "\u4e00" <= ch <= "\u9fff")
    tokens.update(cjk[i : i + 2] for i in range(len(cjk) - 1))
    return tokens


def lexical_overlap(query: str, text: str) -> float:
    """Return the 0-1 lexical overlap ratio between query and text.

    返回查询与文本之间的 0-1 词法重叠率。
    """
    query_terms = tokenize_terms(query)
    if not query_terms:
        return 0.0
    text_terms = tokenize_terms(text)
    return len(query_terms & text_terms) / len(query_terms)


def clip01(value: float) -> float:
    """Clamp a value into the 0-1 range for score normalization.

    将数值裁剪到 0-1 区间，用于分数归一化。
    """
    return max(0.0, min(1.0, value))


def l1_base(vector_score: float, lexical: float) -> float:
    """Normalized L1 episodic base score in 0-1."""
    return 0.6 * clip01(vector_score) + 0.4 * lexical


def l2_entity_base(confidence: float, lexical: float) -> float:
    """Normalized L2 profile/fact base score in 0-1."""
    return 0.7 * clip01(confidence) + 0.3 * lexical


def l2_event_base(importance: int, lexical: float) -> float:
    """Normalized L2 event base score in 0-1."""
    return 0.6 * clip01(importance / 10.0) + 0.4 * lexical


def l2_summary_base(lexical: float, vector_score: float) -> float:
    """Normalized L2 summary base score in 0-1."""
    return max(lexical, 0.6 * clip01(vector_score))


def sort_results(results: list[QueryResultItem]) -> list[QueryResultItem]:
    """Sort results by score desc, then created_at desc for deterministic order.

    按分数降序、创建时间降序排序，保证同分结果顺序确定。
    """
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    return sorted(
        results,
        key=lambda item: (item.score, item.created_at or epoch),
        reverse=True,
    )


def detect_query_intents(query: str) -> set[str]:
    """Infer coarse search intents from the user query.

    根据用户查询推断粗粒度检索意图。
    """
    lowered = query.lower()
    terms = {term for term in re.split(r"\W+", lowered) if term}
    intents = set()
    for intent, keywords in INTENT_KEYWORDS.items():
        if terms & keywords:
            intents.add(intent)
    if not intents:
        intents = {"fact", "event"}
    return intents


def freshness_bonus(created_at: datetime | None, horizon_days: int = 365) -> float:
    """Reward newer memories with a bounded freshness bonus.

    为较新的记忆提供一个有上限的新鲜度加分。
    """
    if created_at is None:
        return 0.0
    now = datetime.now(timezone.utc)
    timestamp = created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    freshness = max(0.0, 1.0 - min(age_days / horizon_days, 1.0))
    return freshness * 0.25


def verification_decay(verified_at: datetime | None, stale_days: int = 365) -> float:
    """Decay trust for memories that have not been re-verified recently.

    对长期未再次验证的记忆降低可信度权重。
    """
    if verified_at is None:
        return 0.55
    now = datetime.now(timezone.utc)
    timestamp = verified_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    decay = min(age_days / stale_days, 1.0)
    return 1.0 - decay * 0.45


def intent_bonus(source: str, intents: set[str]) -> float:
    """Boost items whose memory type matches the inferred intents.

    为与推断意图匹配的记忆类型增加额外分值。
    """
    mapped = SOURCE_INTENT.get(source)
    if mapped == "summary" and "summary" in intents:
        return 0.7
    if mapped in intents:
        return 0.45
    if source == "l1_episodic" and "summary" in intents:
        return 0.1
    if source == "l2_event" and "summary" in intents:
        return 0.05
    return 0.0


def rank_score(
    base_score: float, source: str, created_at: datetime | None, intents: set[str]
) -> float:
    """Rank an item with intent relevance plus freshness.

    结合意图匹配与新鲜度对结果进行排序。
    """
    return base_score + intent_bonus(source, intents) + freshness_bonus(created_at)


def rank_verified_score(
    base_score: float,
    source: str,
    created_at: datetime | None,
    verified_at: datetime | None,
    intents: set[str],
) -> float:
    """Rank a verified item with decay, intent relevance, and freshness.

    对已验证结果结合衰减、意图和新鲜度进行排序。
    """
    return (
        base_score * verification_decay(verified_at)
        + intent_bonus(source, intents)
        + freshness_bonus(created_at)
    )

