from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from datasets import Dataset
from openai import AsyncOpenAI, OpenAI
from sqlmodel import Session, select

from mems.config import settings
from mems.infrastructure.persistence.database import engine
from mems.infrastructure.persistence.models import (
    MemsL1Episodic,
    MemsL2ConflictLog,
    MemsL2Event,
    MemsL2Fact,
    MemsL2ProfileItem,
    MemsL2Summary,
)
from mems.interfaces.bootstrap import (
    make_archive_service,
    make_distill_service,
    trigger_redistill,
)
from mems.infrastructure.external.redis_service import redis_service
from mems.infrastructure.external.vector_service import get_vector_service

try:
    from ragas import evaluate
    from ragas.embeddings.base import BaseRagasEmbeddings
    from ragas.llms import llm_factory
    from ragas.metrics import context_recall, faithfulness

    try:
        from ragas.metrics import answer_relevance as answer_relevance_metric
    except ImportError:
        from ragas.metrics import answer_relevancy as answer_relevance_metric
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Missing RAGAS dependencies. Run `uv sync` first so `ragas` and `datasets` are installed."
    ) from exc


REPORT_DIR = Path("benchmark_reports")
DEFAULT_BASE_URL = "http://127.0.0.1:8210"
DEFAULT_TOP_K = 8
DEFAULT_RESULTS_PATH = REPORT_DIR / "mems_ragas_results_latest.json"
DEFAULT_METRICS = "faithfulness,answer_relevance,context_recall"


@dataclass(frozen=True)
class EvaluatorConfig:
    api_key: str
    base_url: str
    model: str
    embedding_model: str
    embedding_dimensions: int | None
    timeout: int
    embeddings_url: str | None = None


def get_answer_llm_config() -> EvaluatorConfig:
    """Return the main LLM config used for answer generation.

    返回用于 answer generation 的主 LLM 配置。
    """
    return EvaluatorConfig(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.OPENAI_MODEL,
        embedding_model=settings.OPENAI_EMBEDDING_MODEL,
        embedding_dimensions=None,
        timeout=settings.OPENAI_TIMEOUT,
    )


def get_ragas_evaluator_config() -> EvaluatorConfig:
    """RAGAS_* env vars take precedence; fall back to the shared OpenAI settings.

    RAGAS_* 环境变量优先，缺失时回退到共享的 OpenAI 配置；不内置任何密钥。
    """
    dimensions_raw = os.getenv("RAGAS_OPENAI_EMBEDDING_DIMENSIONS")
    return EvaluatorConfig(
        api_key=os.getenv("RAGAS_OPENAI_API_KEY") or settings.OPENAI_API_KEY,
        base_url=os.getenv("RAGAS_OPENAI_BASE_URL") or settings.OPENAI_BASE_URL,
        model=os.getenv("RAGAS_OPENAI_MODEL") or settings.OPENAI_MODEL,
        embedding_model=os.getenv("RAGAS_OPENAI_EMBEDDING_MODEL")
        or settings.OPENAI_EMBEDDING_MODEL,
        embedding_dimensions=int(dimensions_raw) if dimensions_raw else None,
        timeout=int(os.getenv("RAGAS_OPENAI_TIMEOUT", str(settings.OPENAI_TIMEOUT))),
        embeddings_url=os.getenv("RAGAS_OPENAI_EMBEDDINGS_URL")
        or f"{os.getenv('RAGAS_OPENAI_BASE_URL') or settings.OPENAI_BASE_URL}/embeddings",
    )


def make_ragas_sync_client(config: EvaluatorConfig) -> OpenAI:
    return OpenAI(
        api_key=config.api_key, base_url=config.base_url, timeout=config.timeout
    )


class OpenAICompatibleEmbeddings(BaseRagasEmbeddings):
    """Minimal embeddings adapter for providers with partial OpenAI compatibility.

    直接使用官方 OpenAI client 调 embeddings 接口，避免 LangChain 在某些兼容层上的
    输入格式转换问题。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int,
        dimensions: int | None = None,
        embeddings_url: str | None = None,
    ):
        super().__init__()
        self._model = model
        self._dimensions = dimensions
        self._api_key = api_key
        self._timeout = timeout
        self._embeddings_url = embeddings_url or f"{base_url}/embeddings"
        # DashScope native embeddings endpoint uses a non-OpenAI request/response shape.
        self._native_dashscope = "/api/v1/services/embeddings" in self._embeddings_url
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._aclient = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self._model}
        if self._dimensions is not None:
            kwargs["dimensions"] = self._dimensions
        return kwargs

    def _native_embed(self, texts: list[str], text_type: str) -> list[list[float]]:
        """Embed via the DashScope native embeddings endpoint."""
        parameters: dict[str, Any] = {"text_type": text_type}
        if self._dimensions is not None:
            parameters["dimension"] = self._dimensions
        response = httpx.post(
            self._embeddings_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "input": {"texts": texts},
                "parameters": parameters,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        items = sorted(
            data["output"]["embeddings"], key=lambda item: item["text_index"]
        )
        return [item["embedding"] for item in items]

    def embed_query(self, text: str) -> list[float]:
        if self._native_dashscope:
            return self._native_embed([text], "query")[0]
        response = self._client.embeddings.create(
            input=[text], **self._request_kwargs()
        )
        return response.data[0].embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._native_dashscope:
            return self._native_embed(texts, "document")
        response = self._client.embeddings.create(input=texts, **self._request_kwargs())
        return [item.embedding for item in response.data]

    async def aembed_query(self, text: str) -> list[float]:
        if self._native_dashscope:
            return await asyncio.to_thread(self.embed_query, text)
        response = await self._aclient.embeddings.create(
            input=[text], **self._request_kwargs()
        )
        return response.data[0].embedding

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._native_dashscope:
            return await asyncio.to_thread(self.embed_documents, texts)
        response = await self._aclient.embeddings.create(
            input=texts, **self._request_kwargs()
        )
        return [item.embedding for item in response.data]


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    scenario_type: str
    language: str
    user_id: str
    scope: str
    background_turns: list[dict[str, str]]
    recent_turns: list[dict[str, str]]
    query: str
    ground_truth: str
    expected_l2_hint: str
    expected_l1_hint: str
    # Stale value that should not appear in recalled evidence (conflict cases).
    # 冲突场景中不应出现在召回证据里的旧值。
    excluded_hint: str = ""


def _make_case(
    *,
    case_id: str,
    scenario_type: str,
    language: str,
    user_id: str,
    scope: str,
    background_turns: list[dict[str, str]],
    recent_turns: list[dict[str, str]],
    query: str,
    ground_truth: str,
    expected_l2_hint: str,
    expected_l1_hint: str,
    excluded_hint: str = "",
) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        scenario_type=scenario_type,
        language=language,
        user_id=user_id,
        scope=scope,
        background_turns=background_turns,
        recent_turns=recent_turns,
        query=query,
        ground_truth=ground_truth,
        expected_l2_hint=expected_l2_hint,
        expected_l1_hint=expected_l1_hint,
        excluded_hint=excluded_hint,
    )


def _build_cross_layer_trace_cases() -> list[BenchmarkCase]:
    templates = [
        (
            "ava",
            "Ava",
            "艾娃",
            "dark futuristic style",
            "暗色未来主义风格",
            "cyberpunk Discord avatar",
            "赛博朋克 Discord 头像",
            "a dark futuristic cyberpunk color",
            "偏暗的未来感赛博风颜色",
        ),
        (
            "ben",
            "Ben",
            "本",
            "minimal monochrome style",
            "极简黑白风格",
            "brutalist portfolio refresh",
            "粗野主义作品集改版",
            "a monochrome color",
            "黑白单色系颜色",
        ),
        (
            "cora",
            "Cora",
            "科拉",
            "warm vintage palette",
            "温暖复古配色",
            "retro podcast cover",
            "复古播客封面",
            "a warm vintage color",
            "温暖复古色系",
        ),
        (
            "dylan",
            "Dylan",
            "迪伦",
            "high-contrast gaming aesthetics",
            "高对比游戏风格",
            "sci-fi launcher redesign",
            "科幻启动器重设计",
            "a high-contrast sci-fi color",
            "高对比科幻风颜色",
        ),
        (
            "ella",
            "Ella",
            "艾拉",
            "soft pastel style",
            "柔和粉彩风格",
            "cozy study app icon",
            "温暖学习应用图标",
            "a soft pastel color",
            "柔和粉彩色",
        ),
    ]
    cases: list[BenchmarkCase] = []
    for index, (
        slug,
        name_en,
        name_zh,
        pref_en,
        pref_zh,
        recent_en,
        recent_zh,
        answer_en,
        answer_zh,
    ) in enumerate(templates, start=1):
        cases.extend(
            [
                _make_case(
                    case_id=f"zh_case_{index:02d}",
                    scenario_type="cross_layer_trace",
                    language="zh",
                    user_id=f"trace_zh_user_{slug}",
                    scope="trace_zh",
                    background_turns=[
                        {
                            "role": "user",
                            "content": f"2024 年我提过自己一直偏好{pref_zh}。",
                        },
                        {
                            "role": "assistant",
                            "content": f"收到，我会记住你喜欢{pref_zh}。",
                        },
                    ],
                    recent_turns=[
                        {
                            "role": "user",
                            "content": f"2026 年我说想把头像做成{recent_zh}。",
                        },
                        {
                            "role": "assistant",
                            "content": "明白，我会把这个新场景和你过去的审美一起考虑。",
                        },
                    ],
                    query="根据我过去的审美，这个 Discord 按钮应该偏向什么颜色风格？",
                    ground_truth=f"建议使用{answer_zh}，因为它同时符合你过去的{pref_zh}偏好和最近的{recent_zh}场景。",
                    expected_l2_hint=pref_zh,
                    expected_l1_hint=recent_zh,
                ),
                _make_case(
                    case_id=f"en_case_{index:02d}",
                    scenario_type="cross_layer_trace",
                    language="en",
                    user_id=f"trace_en_user_{slug}",
                    scope="trace_en",
                    background_turns=[
                        {
                            "role": "user",
                            "content": f"Back in 2024 I said I prefer a {pref_en}.",
                        },
                        {
                            "role": "assistant",
                            "content": f"Understood. I will remember your preference for a {pref_en}.",
                        },
                    ],
                    recent_turns=[
                        {
                            "role": "user",
                            "content": f"In 2026 I said I want a {recent_en}.",
                        },
                        {
                            "role": "assistant",
                            "content": "Got it. I will combine that new scenario with your past aesthetic preference.",
                        },
                    ],
                    query="Based on my past taste, what color style should this Discord button lean toward?",
                    ground_truth=f"Recommend {answer_en}, because it matches your long-term {pref_en} preference and your recent {recent_en} idea.",
                    expected_l2_hint=pref_en,
                    expected_l1_hint=recent_en,
                ),
            ]
        )
    return cases


def _build_recency_conflict_cases() -> list[BenchmarkCase]:
    templates = [
        (
            "city",
            "I moved to Shanghai.",
            "我搬到上海了。",
            "Shanghai",
            "上海",
            "What city do I live in now?",
            "我现在住在哪个城市？",
        ),
        (
            "lang",
            "My default working language is Chinese now.",
            "我现在默认工作语言是中文。",
            "Chinese",
            "中文",
            "What language should you use with me now?",
            "你现在应该用什么语言和我沟通？",
        ),
        (
            "diet",
            "I avoid sugar now.",
            "我现在开始控糖了。",
            "sugar-free",
            "控糖",
            "What drink suits me now?",
            "我现在适合喝什么饮料？",
        ),
        (
            "role",
            "I am now a product lead.",
            "我现在是产品负责人。",
            "product lead",
            "产品负责人",
            "What is my current role?",
            "我现在的职位是什么？",
        ),
        (
            "timezone",
            "I work in UTC+8 now.",
            "我现在按照 UTC+8 工作。",
            "UTC+8",
            "UTC+8",
            "What timezone am I in now?",
            "我现在在哪个时区？",
        ),
    ]
    cases: list[BenchmarkCase] = []
    for offset, (
        slug,
        new_en,
        new_zh,
        latest_en,
        latest_zh,
        query_en,
        query_zh,
    ) in enumerate(templates, start=6):
        cases.extend(
            [
                _make_case(
                    case_id=f"zh_case_{offset:02d}",
                    scenario_type="recency_conflict",
                    language="zh",
                    user_id=f"conflict_zh_user_{slug}",
                    scope="conflict_zh",
                    background_turns=[
                        {"role": "user", "content": "我之前住在北京。"},
                        {
                            "role": "assistant",
                            "content": "收到，我记得你之前住在北京。",
                        },
                    ],
                    recent_turns=[
                        {"role": "user", "content": new_zh},
                        {
                            "role": "assistant",
                            "content": f"明白，我会用这个更新后的信息：{latest_zh}。",
                        },
                    ],
                    query=query_zh,
                    ground_truth=f"你现在的最新信息是{latest_zh}。",
                    expected_l2_hint=latest_zh,
                    expected_l1_hint=new_zh,
                    excluded_hint="北京",
                ),
                _make_case(
                    case_id=f"en_case_{offset:02d}",
                    scenario_type="recency_conflict",
                    language="en",
                    user_id=f"conflict_en_user_{slug}",
                    scope="conflict_en",
                    background_turns=[
                        {"role": "user", "content": "I used to live in Beijing."},
                        {
                            "role": "assistant",
                            "content": "Understood. I remember that your earlier location was Beijing.",
                        },
                    ],
                    recent_turns=[
                        {"role": "user", "content": new_en},
                        {
                            "role": "assistant",
                            "content": f"Got it. I will use the updated fact: {latest_en}.",
                        },
                    ],
                    query=query_en,
                    ground_truth=f"Your latest current fact is {latest_en}.",
                    expected_l2_hint=latest_en,
                    expected_l1_hint=new_en,
                    excluded_hint="Beijing",
                ),
            ]
        )
    return cases


def _build_implicit_preference_cases() -> list[BenchmarkCase]:
    templates = [
        (
            "coffee",
            ["无糖拿铁怎么做？", "有没有低糖咖啡推荐？"],
            [
                "How do I make a sugar-free latte?",
                "Do you have a low-sugar coffee recommendation?",
            ],
            "无糖咖啡",
            "sugar-free coffee",
        ),
        (
            "sleep",
            ["最近晚上总是睡不好。", "有没有更温和的助眠方法？"],
            ["I haven't been sleeping well lately.", "Do you know gentler sleep aids?"],
            "低刺激助眠建议",
            "low-stimulation sleep aid",
        ),
        (
            "focus",
            ["我想减少会议时间。", "有没有更专注的工作安排方式？"],
            ["I want fewer meetings.", "Do you know a more focused work setup?"],
            "更专注、少会议的安排",
            "a focused low-meeting plan",
        ),
        (
            "diet",
            ["我午饭后总犯困。", "我想吃得更清淡一点。"],
            ["I get sleepy after lunch.", "I want lighter meals."],
            "清淡、低负担饮食",
            "light low-burden meals",
        ),
        (
            "learning",
            ["我更喜欢看例子而不是看定义。", "能不能先给我一个直观示例？"],
            [
                "I learn better from examples than definitions.",
                "Can you show me an intuitive example first?",
            ],
            "先举例再解释",
            "examples before definitions",
        ),
    ]
    cases: list[BenchmarkCase] = []
    for offset, (slug, obs_zh, obs_en, pref_zh, pref_en) in enumerate(
        templates, start=11
    ):
        cases.extend(
            [
                _make_case(
                    case_id=f"zh_case_{offset:02d}",
                    scenario_type="implicit_preference",
                    language="zh",
                    user_id=f"implicit_zh_user_{slug}",
                    scope="implicit_zh",
                    background_turns=[
                        {"role": "user", "content": obs_zh[0]},
                        {"role": "assistant", "content": "收到，我记下来了。"},
                    ],
                    recent_turns=[
                        {"role": "user", "content": obs_zh[1]},
                        {
                            "role": "assistant",
                            "content": f"明白，这说明你可能偏好{pref_zh}。",
                        },
                    ],
                    query="根据我之前说过的话，给我一个合适的建议。",
                    ground_truth=f"建议优先考虑{pref_zh}。",
                    expected_l2_hint=pref_zh,
                    expected_l1_hint=obs_zh[1],
                ),
                _make_case(
                    case_id=f"en_case_{offset:02d}",
                    scenario_type="implicit_preference",
                    language="en",
                    user_id=f"implicit_en_user_{slug}",
                    scope="implicit_en",
                    background_turns=[
                        {"role": "user", "content": obs_en[0]},
                        {"role": "assistant", "content": "Got it. I noted that down."},
                    ],
                    recent_turns=[
                        {"role": "user", "content": obs_en[1]},
                        {
                            "role": "assistant",
                            "content": f"Understood. That suggests you likely prefer {pref_en}.",
                        },
                    ],
                    query="Based on what I said before, what would you recommend?",
                    ground_truth=f"Recommend {pref_en}.",
                    expected_l2_hint=pref_en,
                    expected_l1_hint=obs_en[1],
                ),
            ]
        )
    return cases


def _build_needle_in_haystack_cases() -> list[BenchmarkCase]:
    templates = [
        (
            "error",
            "ERR-4917",
            "错误码 ERR-4917 出现在 payment_worker.py 第 83 行。",
            "Error code ERR-4917 appeared in payment_worker.py line 83.",
        ),
        (
            "trace",
            "TRACE-8F2A",
            "Trace ID TRACE-8F2A 对应的是库存同步任务。",
            "Trace ID TRACE-8F2A belongs to the inventory sync job.",
        ),
        (
            "order",
            "ORDER-22091",
            "订单号 ORDER-22091 是这批日志里提到的异常订单。",
            "Order ID ORDER-22091 is the anomalous order mentioned in the logs.",
        ),
        (
            "commit",
            "c4f91ab",
            "可疑提交哈希是 c4f91ab。",
            "The suspicious commit hash is c4f91ab.",
        ),
        (
            "flag",
            "FLAG_CYBER_DARK",
            "相关 feature flag 是 FLAG_CYBER_DARK。",
            "The relevant feature flag is FLAG_CYBER_DARK.",
        ),
    ]
    noise_zh = [f"DEBUG 日志片段 {i}: module_{i} 正常运行。" for i in range(1, 21)]
    noise_en = [
        f"DEBUG log snippet {i}: module_{i} is operating normally."
        for i in range(1, 21)
    ]
    cases: list[BenchmarkCase] = []
    for offset, (slug, needle, hit_zh, hit_en) in enumerate(templates, start=16):
        cases.extend(
            [
                _make_case(
                    case_id=f"zh_case_{offset:02d}",
                    scenario_type="needle_in_haystack",
                    language="zh",
                    user_id=f"needle_zh_user_{slug}",
                    scope="needle_zh",
                    background_turns=[
                        {"role": "user", "content": content}
                        for content in noise_zh[:10]
                    ],
                    recent_turns=[
                        *[
                            {"role": "user", "content": content}
                            for content in noise_zh[10:]
                        ],
                        {"role": "user", "content": hit_zh},
                    ],
                    query="我在那堆日志里提到的错误代码或标识符是什么？",
                    ground_truth=f"日志里提到的关键标识符是 {needle}。",
                    expected_l2_hint=needle,
                    expected_l1_hint=needle,
                ),
                _make_case(
                    case_id=f"en_case_{offset:02d}",
                    scenario_type="needle_in_haystack",
                    language="en",
                    user_id=f"needle_en_user_{slug}",
                    scope="needle_en",
                    background_turns=[
                        {"role": "user", "content": content}
                        for content in noise_en[:10]
                    ],
                    recent_turns=[
                        *[
                            {"role": "user", "content": content}
                            for content in noise_en[10:]
                        ],
                        {"role": "user", "content": hit_en},
                    ],
                    query="What was the error code or identifier mentioned in those logs?",
                    ground_truth=f"The key identifier mentioned in the logs is {needle}.",
                    expected_l2_hint=needle,
                    expected_l1_hint=needle,
                ),
            ]
        )
    return cases


HYGIENE_SCENARIOS = {
    "l0_recall",
    "archive_exclusion",
    "identity_isolation",
}

EXTENDED_SCENARIOS = {
    "redistill_upgrade",
    "l0_pipeline_consume",
}


def _build_l0_recall_cases() -> list[BenchmarkCase]:
    """Recent hot memory should be recalled from L0 without distillation."""
    return [
        _make_case(
            case_id="l0_recall_01",
            scenario_type="l0_recall",
            language="en",
            user_id="l0_user_a",
            scope="l0",
            background_turns=[],
            recent_turns=[
                {
                    "role": "user",
                    "content": "Just so you know, my favorite color is teal.",
                },
                {"role": "assistant", "content": "Got it, teal."},
            ],
            query="What is my favorite color?",
            ground_truth="teal",
            expected_l2_hint="",
            expected_l1_hint="teal",
        )
    ]


def _build_archive_exclusion_cases() -> list[BenchmarkCase]:
    """Archived L1 must not be recalled by default online queries."""
    return [
        _make_case(
            case_id="archive_exclusion_01",
            scenario_type="archive_exclusion",
            language="en",
            user_id="archive_user_a",
            scope="archive",
            background_turns=[
                {
                    "role": "user",
                    "content": "Years ago my old address was in Beijing.",
                },
            ],
            recent_turns=[],
            query="Where did I used to live?",
            ground_truth="No online evidence (archived).",
            expected_l2_hint="",
            expected_l1_hint="Beijing",
        )
    ]


def _build_identity_isolation_cases() -> list[BenchmarkCase]:
    """Memories of one user must not leak into another user's query."""
    return [
        _make_case(
            case_id="identity_isolation_01",
            scenario_type="identity_isolation",
            language="en",
            user_id="isolation_user_a",
            scope="isolation",
            background_turns=[
                {"role": "user", "content": "I love hiking on weekends."},
            ],
            recent_turns=[],
            query="What do I like to do?",
            ground_truth="No cross-user evidence.",
            expected_l2_hint="",
            expected_l1_hint="hiking",
        )
    ]


def _build_redistill_upgrade_cases() -> list[BenchmarkCase]:
    """Archived records can be re-distilled into upgraded L2."""
    return [
        _make_case(
            case_id="redistill_upgrade_01",
            scenario_type="redistill_upgrade",
            language="en",
            user_id="redistill_user_a",
            scope="redistill",
            background_turns=[
                {
                    "role": "user",
                    "content": "My favorite programming language is Python.",
                },
            ],
            recent_turns=[],
            query="What is my favorite programming language?",
            ground_truth="Python",
            expected_l2_hint="Python",
            expected_l1_hint="",
        )
    ]


def _build_l0_pipeline_cases() -> list[BenchmarkCase]:
    """Enabled L0 output pipeline publishes one event per write."""
    return [
        _make_case(
            case_id="l0_pipeline_consume_01",
            scenario_type="l0_pipeline_consume",
            language="en",
            user_id="pipeline_user_a",
            scope="pipeline",
            background_turns=[],
            recent_turns=[
                {"role": "user", "content": "Stream event marker 42"},
            ],
            query="stream event marker 42",
            ground_truth="event received",
            expected_l2_hint="",
            expected_l1_hint="stream event marker 42",
        )
    ]


def build_benchmark_cases(extended: bool = False) -> list[BenchmarkCase]:
    cases = (
        _build_cross_layer_trace_cases()
        + _build_recency_conflict_cases()
        + _build_implicit_preference_cases()
        + _build_needle_in_haystack_cases()
        + _build_l0_recall_cases()
        + _build_archive_exclusion_cases()
        + _build_identity_isolation_cases()
    )
    if extended:
        cases += _build_redistill_upgrade_cases()
        cases += _build_l0_pipeline_cases()
    return cases


async def post_json(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    response = await client.post(url, json=payload)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json(), elapsed_ms


async def get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    response = await client.get(url, params=params)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json(), elapsed_ms


async def wait_for_benchmark_dependencies(
    client: httpx.AsyncClient,
    base_url: str,
    timeout_seconds: float = 90.0,
    poll_interval_seconds: float = 2.0,
) -> None:
    """Wait until Mems and Qdrant are ready for benchmark traffic.

    等待 Mems 依赖组件，尤其是 Qdrant，进入可承载 benchmark 的状态。
    """
    warmup_identity = {
        "agent_id": "benchmark_warmup_agent",
        "session_id": "benchmark_warmup_session",
        "user_id": "benchmark_warmup_user",
        "scope": "benchmark_warmup",
    }
    deadline = time.perf_counter() + timeout_seconds
    last_error = "dependency warm-up did not start"
    while time.perf_counter() < deadline:
        try:
            health_response, _ = await get_json(
                client, f"{base_url}/v1/mems/health", {}
            )
            if health_response.get("status") != "healthy":
                last_error = f"health returned {health_response}"
                await asyncio.sleep(poll_interval_seconds)
                continue

            status_response, _ = await get_json(
                client, f"{base_url}/v1/mems/status", {}
            )
            checks = status_response.get("checks", {})
            unhealthy = {
                name: value.get("detail") or value.get("status")
                for name, value in checks.items()
                if value.get("status") != "healthy"
            }
            if unhealthy:
                last_error = f"status checks not ready: {unhealthy}"
                await asyncio.sleep(poll_interval_seconds)
                continue

            await post_json(
                client,
                f"{base_url}/v1/mems/write",
                {
                    **warmup_identity,
                    "messages": [{"role": "user", "content": "benchmark warmup"}],
                    "metadata": {"benchmark_warmup": True},
                },
            )
            await post_json(
                client,
                f"{base_url}/v1/mems/query",
                {
                    "agent_id": warmup_identity["agent_id"],
                    "user_id": warmup_identity["user_id"],
                    "scope": warmup_identity["scope"],
                    "query": "benchmark warmup",
                    "top_k": 1,
                },
            )
            await cleanup_agent_data(**warmup_identity)
            return
        except Exception as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
            await asyncio.sleep(poll_interval_seconds)

    raise RuntimeError(
        f"Benchmark dependency warm-up failed before timeout: {last_error}"
    )


async def cleanup_agent_data(
    agent_id: str,
    session_id: str,
    user_id: str,
    scope: str,
) -> None:
    """Best-effort removal of one agent's benchmark data from local storage.

    尽力清理某个 agent 的本地存储数据（L1/L2/向量/L0），避免跨轮污染。
    """
    try:
        await redis_service.delete(
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            scope=scope,
        )
    except Exception:
        pass

    with Session(engine) as session:
        for model in (
            MemsL2ConflictLog,
            MemsL2Event,
            MemsL2Fact,
            MemsL2ProfileItem,
            MemsL2Summary,
            MemsL1Episodic,
        ):
            rows = session.exec(select(model).where(model.agent_id == agent_id)).all()
            for row in rows:
                session.delete(row)
        session.commit()

    vector_service = await get_vector_service()
    try:
        await vector_service.delete_collection(f"agent_{agent_id}")
    except Exception:
        pass


def backdate_agent_records(agent_id: str, days: int) -> None:
    """Backdate an agent's L1 records to simulate old memories.

    将某个 agent 的 L1 记录回填为较早时间，模拟历史记忆，支撑时间语义与归档用例。
    """
    with Session(engine) as session:
        rows = session.exec(
            select(MemsL1Episodic).where(MemsL1Episodic.agent_id == agent_id)
        ).all()
        backdated_at = datetime.now(timezone.utc) - timedelta(days=days)
        for row in rows:
            row.created_at = backdated_at
            session.add(row)
        session.commit()


async def force_distill(agent_id: str) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    with Session(engine) as session:
        service = await make_distill_service(session)
        result = await service.distill(agent_id=agent_id, force=True)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "success": result.success,
        "distilled_count": result.distilled_count,
        "l2_created": result.l2_created,
        "l2_updated": result.l2_updated,
        "message": result.message,
    }, elapsed_ms


async def generate_answer(
    question: str,
    contexts: list[str],
    live_messages: list[dict[str, str]],
    language: str,
    scenario_type: str,
    answer_config: EvaluatorConfig,
) -> tuple[str, float]:
    answer_language = "Chinese" if language == "zh" else "English"
    requirements = [
        "1. Mention only facts directly supported by the evidence.",
        "2. Prefer copying critical details such as time, project, tone, or format from the evidence.",
        "3. Do not invent recommendations beyond what the evidence wording supports.",
        "4. Output exactly one sentence.",
    ]
    if scenario_type == "recency_conflict":
        scenario_guidance = "When older and newer facts conflict, answer only with the latest supported fact. Do not mention stale facts unless the question explicitly asks for history."
    elif scenario_type == "implicit_preference":
        scenario_guidance = "Infer only the narrow preference that is clearly repeated or supported by the evidence. Keep the recommendation concrete but minimal."
    elif scenario_type == "cross_layer_trace":
        scenario_guidance = (
            "For cross-layer questions, combine one long-term preference with one recent session detail in the same sentence. "
            "State the concrete style or color direction that follows from both pieces of evidence, and briefly justify it with both the long-term preference and the recent scenario."
        )
        requirements = [
            "1. Mention only facts directly supported by the evidence.",
            "2. Use both a long-term memory clue and a recent-session clue in the same sentence.",
            "3. Name the resulting style or color direction as concretely as the evidence supports.",
            "4. Briefly justify the answer with both clues.",
            "5. Output exactly one sentence.",
        ]
    else:
        scenario_guidance = "For style or identifier questions, stay close to the evidence wording. If the evidence supports only an abstract style direction, answer with that style direction instead of inventing a more specific recommendation."
    prompt = [
        {
            "role": "system",
            "content": (
                "Answer only from the provided Mems evidence. Use recent session details and long-term memory only when they are explicitly supported by the evidence. "
                "Do not offer extra help, suggestions, or speculation. Do not add facts that are not present in the evidence. "
                f"If evidence is insufficient, say exactly that. Return a single concise sentence in {answer_language}. {scenario_guidance}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Live context messages:\n{json.dumps(live_messages, ensure_ascii=False, indent=2)}\n\n"
                f"Retrieved memory contexts:\n{json.dumps(contexts, ensure_ascii=False, indent=2)}\n\n"
                "Requirements:\n" + "\n".join(requirements)
            ),
        },
    ]
    started = time.perf_counter()
    client = AsyncOpenAI(
        api_key=answer_config.api_key,
        base_url=answer_config.base_url,
        timeout=float(answer_config.timeout),
    )
    response = await client.chat.completions.create(
        model=answer_config.model,
        messages=prompt,
        temperature=0.0,
    )
    answer = response.choices[0].message.content or ""
    elapsed_ms = (time.perf_counter() - started) * 1000
    return answer.strip(), elapsed_ms


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * ratio
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (
        index - lower
    )


def format_ms(value: float) -> str:
    return f"{value:.1f} ms"


def format_score(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:.4f}"


def _mean_std(values: list[float]) -> tuple[float | None, float | None]:
    """Return (mean, std); std is 0.0 for single values."""
    if not values:
        return None, None
    mean = statistics.mean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, std


def _fmt_value(row: dict[str, Any], field: str) -> str:
    """Format a possibly-aggregated numeric field as 'mean±std' or 'n/a'."""
    value = row.get(field)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    std = row.get(f"{field}_std")
    if std:
        return f"{value:.4f}±{std:.4f}"
    return f"{value:.4f}"


def aggregate_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group raw rows by case_id across trials and aggregate numeric fields.

    将多次 trial 的原始结果按 case_id 聚合，数值字段输出均值与标准差。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        grouped.setdefault(row["case_id"], []).append(row)

    aggregated: list[dict[str, Any]] = []
    for case_id, rows in grouped.items():
        base = dict(rows[0])
        base.pop("trial", None)
        base["trial_count"] = len(rows)
        statuses = {row.get("status") for row in rows}
        if statuses == {"ok"}:
            base["status"] = "ok"
        elif "failed" in statuses:
            base["status"] = "failed"
        elif statuses == {"skipped"}:
            base["status"] = "skipped"
        else:
            base["status"] = "mixed"

        for field in (
            "write_ms",
            "distill_ms",
            "query_ms",
            "answer_ms",
            "end_to_end_ms",
            "faithfulness",
            "answer_relevance",
            "context_recall",
            "l1_hit",
            "l2_hit",
            "conflict_resolved",
            "l1_rr",
            "l2_rr",
        ):
            values = [
                row[field]
                for row in rows
                if isinstance(row.get(field), (int, float))
                and not (
                    isinstance(row.get(field), float)
                    and math.isnan(row.get(field))
                )
            ]
            if values:
                mean, std = _mean_std(values)
                base[field] = mean
                base[f"{field}_std"] = std

        base["notes"] = ", ".join(
            sorted(
                {
                    note
                    for row in rows
                    for note in (row.get("notes") or "").split(", ")
                    if note
                }
            )
        )
        aggregated.append(base)
    return aggregated


def build_report(
    results: list[dict[str, Any]],
    report_path: Path,
    base_url: str,
    top_k: int,
    answer_config: EvaluatorConfig,
    ragas_config: EvaluatorConfig,
) -> None:
    aggregated = aggregate_results(results)
    successful = [row for row in aggregated if row.get("status") == "ok"]
    conflict_rows = [
        row
        for row in successful
        if row.get("scenario_type") == "recency_conflict"
    ]

    def _metric_summary(rows: list[dict[str, Any]], field: str) -> str:
        values = [
            row[field]
            for row in rows
            if isinstance(row.get(field), (int, float))
            and not (isinstance(row.get(field), float) and math.isnan(row.get(field)))
        ]
        mean, std = _mean_std(values)
        if mean is None:
            return "n/a"
        if std:
            return f"{mean:.4f}±{std:.4f}"
        return f"{mean:.4f}"

    def _e2e_summary(rows: list[dict[str, Any]]) -> tuple[str, str, str]:
        values = [row["end_to_end_ms"] for row in rows]
        mean, std = _mean_std(values)
        mean_txt = f"{mean:.1f} ms" if mean is not None else "n/a"
        if std:
            mean_txt = f"{mean_txt}±{std:.1f}"
        return (
            mean_txt,
            f"{percentile(values, 0.5):.1f} ms",
            f"{percentile(values, 0.95):.1f} ms",
        )

    def _language_summary(language: str) -> list[str]:
        rows = [row for row in successful if row.get("language") == language]
        label = "Chinese" if language == "zh" else "English"
        return [
            f"## {label} Summary",
            f"- case_count: {len(rows)}",
            f"- avg_faithfulness: {_metric_summary(rows, 'faithfulness')}",
            f"- avg_answer_relevance: {_metric_summary(rows, 'answer_relevance')}",
            f"- avg_context_recall: {_metric_summary(rows, 'context_recall')}",
            f"- avg_end_to_end_ms: {_e2e_summary(rows)[0]}",
            "",
        ]

    def _scenario_summary(scenario_type: str) -> list[str]:
        rows = [row for row in successful if row.get("scenario_type") == scenario_type]
        return [
            f"## {scenario_type.replace('_', ' ').title()} Summary",
            f"- case_count: {len(rows)}",
            f"- avg_faithfulness: {_metric_summary(rows, 'faithfulness')}",
            f"- avg_answer_relevance: {_metric_summary(rows, 'answer_relevance')}",
            f"- avg_context_recall: {_metric_summary(rows, 'context_recall')}",
            f"- avg_end_to_end_ms: {_e2e_summary(rows)[0]}",
            "",
        ]

    by_status: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    skip_reasons: dict[str, int] = {}
    for row in aggregated:
        status = row.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        if status == "failed":
            err = row.get("error_type") or "unknown"
            error_counts[err] = error_counts.get(err, 0) + 1
        if status == "skipped":
            reason = row.get("notes") or "unknown"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    failure_lines = ["## Failure Summary", ""]
    failure_lines.append(f"- status_counts: {by_status}")
    if error_counts:
        failure_lines.append("- by_error_type:")
        failure_lines += [
            f"  - {key}: {count}" for key, count in sorted(error_counts.items())
        ]
    if skip_reasons:
        failure_lines.append("- skipped_reasons:")
        failure_lines += [
            f"  - {key}: {count}" for key, count in sorted(skip_reasons.items())
        ]
    failure_lines.append("")

    retrieval_lines = [
        "## Retrieval Metrics",
        "",
        f"- avg_l1_recall@k: {_metric_summary(successful, 'l1_hit')}",
        f"- avg_l2_recall@k: {_metric_summary(successful, 'l2_hit')}",
        f"- avg_l1_mrr: {_metric_summary(successful, 'l1_rr')}",
        f"- avg_l2_mrr: {_metric_summary(successful, 'l2_rr')}",
        f"- recency_conflict_resolved: {_metric_summary(conflict_rows, 'conflict_resolved')}",
        "",
    ]

    mean_e2e, p50_e2e, p95_e2e = _e2e_summary(successful)
    summary_lines = [
        "# Mems RAGAS Benchmark Report",
        "",
        "## Summary",
        f"- total_cases: {len(aggregated)}",
        f"- successful_cases: {len(successful)}",
        f"- failed_cases: {by_status.get('failed', 0)}",
        f"- skipped_cases: {by_status.get('skipped', 0)}",
        f"- trials_per_case: {aggregated[0].get('trial_count', 1) if aggregated else 1}",
        f"- avg_faithfulness: {_metric_summary(successful, 'faithfulness')}",
        f"- avg_answer_relevance: {_metric_summary(successful, 'answer_relevance')}",
        f"- avg_context_recall: {_metric_summary(successful, 'context_recall')}",
        f"- avg_end_to_end_ms: {mean_e2e}",
        f"- p50_end_to_end_ms: {p50_e2e}",
        f"- p95_end_to_end_ms: {p95_e2e}",
        "",
        "## Environment",
        f"- base_url: `{base_url}`",
        f"- top_k: {top_k}",
        f"- answer_llm_model: `{answer_config.model}`",
        f"- answer_llm_base_url: `{answer_config.base_url}`",
        f"- ragas_llm_model: `{ragas_config.model}`",
        f"- ragas_llm_base_url: `{ragas_config.base_url}`",
        f"- ragas_embedding_model: `{ragas_config.embedding_model}`",
        f"- generated_at: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        *_language_summary("zh"),
        *_language_summary("en"),
        *_scenario_summary("cross_layer_trace"),
        *_scenario_summary("recency_conflict"),
        *_scenario_summary("implicit_preference"),
        *_scenario_summary("needle_in_haystack"),
        *_scenario_summary("l0_recall"),
        *_scenario_summary("archive_exclusion"),
        *_scenario_summary("identity_isolation"),
        *_scenario_summary("redistill_upgrade"),
        *_scenario_summary("l0_pipeline_consume"),
        *retrieval_lines,
        *failure_lines,
        "## Per-case Results",
        "",
        "| case_id | scenario | language | status | faithfulness | answer_relevance | context_recall | l1_hit | l2_hit | query_ms | e2e_ms | notes |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for row in aggregated:
        summary_lines.append(
            "| {case_id} | {scenario} | {language} | {status} | {faithfulness} | {answer_relevance} | {context_recall} | {l1_hit} | {l2_hit} | {query_ms} | {e2e_ms} | {notes} |".format(
                case_id=row["case_id"],
                scenario=row.get("scenario_type", ""),
                language=row.get("language", ""),
                status=row.get("status", "unknown"),
                faithfulness=_fmt_value(row, "faithfulness"),
                answer_relevance=_fmt_value(row, "answer_relevance"),
                context_recall=_fmt_value(row, "context_recall"),
                l1_hit=_fmt_value(row, "l1_hit"),
                l2_hit=_fmt_value(row, "l2_hit"),
                query_ms=f"{row.get('query_ms', 0.0):.1f}",
                e2e_ms=f"{row.get('end_to_end_ms', 0.0):.1f}",
                notes=(row.get("notes") or "").replace("|", "/"),
            )
        )

    summary_lines.extend(["", "## Detailed Results", ""])
    for row in results:
        summary_lines.extend(
            [
                f"### {row['case_id']} (trial {row.get('trial', 1)})",
                f"- status: {row.get('status', 'unknown')}",
                f"- scenario_type: {row.get('scenario_type', '')}",
                f"- language: {row.get('language', '')}",
                f"- question: {row['question']}",
                f"- ground_truth: {row['ground_truth']}",
                f"- answer: {row.get('answer', '')}",
                f"- faithfulness: {format_score(row.get('faithfulness'))}",
                f"- answer_relevance: {format_score(row.get('answer_relevance'))}",
                f"- context_recall: {format_score(row.get('context_recall'))}",
                f"- l1_hit: {row.get('l1_hit', 'n/a')} (rank {row.get('l1_rank', '-')})",
                f"- l2_hit: {row.get('l2_hit', 'n/a')} (rank {row.get('l2_rank', '-')})",
                f"- conflict_resolved: {row.get('conflict_resolved', 'n/a')}",
                f"- write_ms: {format_ms(row.get('write_ms', 0.0))}",
                f"- distill_ms: {format_ms(row.get('distill_ms', 0.0))}",
                f"- query_ms: {format_ms(row.get('query_ms', 0.0))}",
                f"- answer_ms: {format_ms(row.get('answer_ms', 0.0))}",
                f"- ragas_ms: {format_ms(row.get('ragas_ms', 0.0))}",
                f"- end_to_end_ms: {format_ms(row.get('end_to_end_ms', 0.0))}",
                f"- notes: {row.get('notes', '')}",
                "- retrieved_sources: " + ", ".join(row.get("retrieved_sources", [])),
                "- retrieved_contexts:",
                "```text",
                "\n\n".join(row.get("contexts", [])) or "",
                "```",
                "",
            ]
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(summary_lines), encoding="utf-8")


def write_results_snapshot(results: list[dict[str, Any]], results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_results_snapshot(results_path: Path) -> list[dict[str, Any]]:
    return json.loads(results_path.read_text(encoding="utf-8"))


def _case_identity(case: BenchmarkCase, run_id: str) -> tuple[str, str]:
    """Build run-scoped agent/session ids to avoid cross-run pollution."""
    agent_id = f"benchmark_{run_id}_{case.case_id}"
    session_id = f"benchmark_{run_id}_{case.case_id}"
    return agent_id, session_id


def _hint_metrics(contexts: list[str], hint: str) -> tuple[int, int]:
    """Return (hit, rank) for a hint inside the retrieved contexts.

    返回提示词在召回上下文中的 (是否命中, 首个命中位置 1-based)；未命中 rank=0。
    """
    if not hint:
        return 0, 0
    lowered = hint.lower()
    positions = [index for index, ctx in enumerate(contexts) if lowered in ctx.lower()]
    if not positions:
        return 0, 0
    return 1, positions[0] + 1


def _fail_result(
    case: BenchmarkCase, trial: int, exc: Exception, started: float
) -> dict[str, Any]:
    error_message = str(exc).strip() or repr(exc)
    return {
        "case_id": case.case_id,
        "scenario_type": case.scenario_type,
        "language": case.language,
        "question": case.query,
        "ground_truth": case.ground_truth,
        "trial": trial,
        "ragas_eligible": True,
        "status": "failed",
        "notes": f"pipeline_error: {exc.__class__.__name__}: {error_message}",
        "error_type": exc.__class__.__name__,
        "error_message": error_message,
        "error_traceback": traceback.format_exc(),
        "answer": "",
        "contexts": [],
        "retrieved_sources": [],
        "write_ms": 0.0,
        "distill_ms": 0.0,
        "query_ms": 0.0,
        "answer_ms": 0.0,
        "end_to_end_ms": (time.perf_counter() - started) * 1000,
    }


def _skip_result(
    case: BenchmarkCase, trial: int, note: str, started: float
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "scenario_type": case.scenario_type,
        "language": case.language,
        "question": case.query,
        "ground_truth": case.ground_truth,
        "trial": trial,
        "ragas_eligible": False,
        "status": "skipped",
        "notes": note,
        "answer": "",
        "contexts": [],
        "retrieved_sources": [],
        "write_ms": 0.0,
        "distill_ms": 0.0,
        "query_ms": 0.0,
        "answer_ms": 0.0,
        "end_to_end_ms": (time.perf_counter() - started) * 1000,
    }


async def _run_hygiene_case(
    client: httpx.AsyncClient,
    case: BenchmarkCase,
    base_url: str,
    top_k: int,
    run_id: str,
    trial: int,
) -> dict[str, Any]:
    """Run retrieval-hygiene cases: L0 recall, archive exclusion, isolation."""
    started = time.perf_counter()
    agent_id, session_id = _case_identity(case, run_id)
    contexts: list[str] = []
    sources: list[str] = []
    write_ms = query_ms = 0.0
    try:
        notes: list[str] = []
        if case.scenario_type == "l0_recall":
            _, write_ms = await post_json(
                client,
                f"{base_url}/v1/mems/write",
                {
                    "user_id": case.user_id,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "scope": case.scope,
                    "messages": case.recent_turns,
                },
            )
            query_json, query_ms = await post_json(
                client,
                f"{base_url}/v1/mems/query",
                {
                    "user_id": case.user_id,
                    "agent_id": agent_id,
                    "scope": case.scope,
                    "query": case.query,
                    "top_k": top_k,
                },
            )
            sources = [item["source"] for item in query_json.get("results", [])]
            notes.append("l0_hit" if "l0_hot" in sources else "l0_miss")
        elif case.scenario_type == "archive_exclusion":
            _, write_ms = await post_json(
                client,
                f"{base_url}/v1/mems/write",
                {
                    "user_id": case.user_id,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "scope": case.scope,
                    "messages": case.background_turns,
                },
            )
            backdate_agent_records(agent_id, days=settings.ARCHIVE_DAYS + 30)
            with Session(engine) as session:
                await make_archive_service(session).archive(
                    agent_id=agent_id, days=settings.ARCHIVE_DAYS
                )
            query_json, query_ms = await post_json(
                client,
                f"{base_url}/v1/mems/query",
                {
                    "user_id": case.user_id,
                    "agent_id": agent_id,
                    "scope": case.scope,
                    "query": case.query,
                    "top_k": top_k,
                },
            )
            contexts = [item["content"] for item in query_json.get("results", [])]
            notes.append(
                "archived_leaked"
                if case.expected_l1_hint.lower() in " ".join(contexts).lower()
                else "archived_excluded"
            )
        elif case.scenario_type == "identity_isolation":
            _, write_ms = await post_json(
                client,
                f"{base_url}/v1/mems/write",
                {
                    "user_id": case.user_id,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "scope": case.scope,
                    "messages": case.background_turns,
                },
            )
            other_user = (
                "isolation_user_b"
                if case.user_id == "isolation_user_a"
                else "isolation_user_a"
            )
            query_json, query_ms = await post_json(
                client,
                f"{base_url}/v1/mems/query",
                {
                    "user_id": other_user,
                    "agent_id": agent_id,
                    "scope": case.scope,
                    "query": case.query,
                    "top_k": top_k,
                },
            )
            contexts = [item["content"] for item in query_json.get("results", [])]
            sources = [item["source"] for item in query_json.get("results", [])]
            notes.append(
                "isolation_leak"
                if case.expected_l1_hint.lower() in " ".join(contexts).lower()
                else "isolation_ok"
            )

        return {
            "case_id": case.case_id,
            "scenario_type": case.scenario_type,
            "language": case.language,
            "question": case.query,
            "ground_truth": case.ground_truth,
            "trial": trial,
            "ragas_eligible": False,
            "status": "ok",
            "notes": ", ".join(notes),
            "answer": "",
            "contexts": contexts,
            "retrieved_sources": sources,
            "write_ms": write_ms,
            "distill_ms": 0.0,
            "query_ms": query_ms,
            "answer_ms": 0.0,
            "end_to_end_ms": (time.perf_counter() - started) * 1000,
        }
    except Exception as exc:
        return _fail_result(case, trial, exc, started)
    finally:
        await cleanup_agent_data(agent_id, session_id, case.user_id, case.scope)


async def _run_redistill_case(
    client: httpx.AsyncClient,
    case: BenchmarkCase,
    base_url: str,
    top_k: int,
    run_id: str,
    trial: int,
) -> dict[str, Any]:
    """Extended: re-distill an archived record and verify L2 upgrade."""
    started = time.perf_counter()
    agent_id, session_id = _case_identity(case, run_id)
    try:
        if not settings.OPENAI_API_KEY:
            return _skip_result(case, trial, "redistill_llm_not_configured", started)
        _, write_ms = await post_json(
            client,
            f"{base_url}/v1/mems/write",
            {
                "user_id": case.user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "scope": case.scope,
                "messages": case.background_turns,
            },
        )
        backdate_agent_records(agent_id, days=settings.ARCHIVE_DAYS + 30)
        with Session(engine) as session:
            await make_archive_service(session).archive(
                agent_id=agent_id, days=settings.ARCHIVE_DAYS
            )
        await trigger_redistill(agent_id=agent_id)
        query_json, query_ms = await post_json(
            client,
            f"{base_url}/v1/mems/query",
            {
                "user_id": case.user_id,
                "agent_id": agent_id,
                "scope": case.scope,
                "query": case.query,
                "top_k": top_k,
            },
        )
        contexts = [item["content"] for item in query_json.get("results", [])]
        note = (
            "l2_after_redistill"
            if case.expected_l2_hint.lower() in " ".join(contexts).lower()
            else "redistill_no_effect"
        )
        return {
            "case_id": case.case_id,
            "scenario_type": case.scenario_type,
            "language": case.language,
            "question": case.query,
            "ground_truth": case.ground_truth,
            "trial": trial,
            "ragas_eligible": False,
            "status": "ok",
            "notes": note,
            "answer": "",
            "contexts": contexts,
            "retrieved_sources": [
                item["source"] for item in query_json.get("results", [])
            ],
            "write_ms": write_ms,
            "distill_ms": 0.0,
            "query_ms": query_ms,
            "answer_ms": 0.0,
            "end_to_end_ms": (time.perf_counter() - started) * 1000,
        }
    except Exception as exc:
        return _fail_result(case, trial, exc, started)
    finally:
        await cleanup_agent_data(agent_id, session_id, case.user_id, case.scope)


async def _run_l0_pipeline_case(
    client: httpx.AsyncClient,
    case: BenchmarkCase,
    base_url: str,
    top_k: int,
    run_id: str,
    trial: int,
) -> dict[str, Any]:
    """Extended: verify the L0 output stream receives one event per write."""
    started = time.perf_counter()
    agent_id, session_id = _case_identity(case, run_id)
    try:
        if not settings.L0_PIPELINE_ENABLED:
            return _skip_result(case, trial, "l0_pipeline_not_enabled", started)

        import redis.asyncio as redis

        client_redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
            decode_responses=True,
        )
        try:
            await client_redis.ping()
        except Exception:
            await client_redis.aclose()
            return _skip_result(case, trial, "redis_unavailable", started)

        _, write_ms = await post_json(
            client,
            f"{base_url}/v1/mems/write",
            {
                "user_id": case.user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "scope": case.scope,
                "messages": case.recent_turns,
            },
        )
        entries = await client_redis.xrevrange(
            settings.L0_PIPELINE_STREAM, count=5
        )
        await client_redis.aclose()
        marker = case.expected_l1_hint
        received = any(
            marker.lower() in json.dumps(fields, ensure_ascii=False).lower()
            for _, fields in entries
        )
        return {
            "case_id": case.case_id,
            "scenario_type": case.scenario_type,
            "language": case.language,
            "question": case.query,
            "ground_truth": case.ground_truth,
            "trial": trial,
            "ragas_eligible": False,
            "status": "ok",
            "notes": "event_received" if received else "event_missing",
            "answer": "",
            "contexts": [],
            "retrieved_sources": [],
            "write_ms": write_ms,
            "distill_ms": 0.0,
            "query_ms": 0.0,
            "answer_ms": 0.0,
            "end_to_end_ms": (time.perf_counter() - started) * 1000,
        }
    except Exception as exc:
        return _fail_result(case, trial, exc, started)
    finally:
        await cleanup_agent_data(agent_id, session_id, case.user_id, case.scope)


async def run_case(
    client: httpx.AsyncClient,
    case: BenchmarkCase,
    base_url: str,
    top_k: int,
    answer_config: EvaluatorConfig,
    run_id: str,
    trial: int,
) -> dict[str, Any]:
    """Run one benchmark case end to end.

    卫生用例与扩展用例走专用执行路径；常规用例走
    write(background) → backdate → distill → write(recent) → query → answer。
    """
    started = time.perf_counter()
    agent_id, session_id = _case_identity(case, run_id)

    if case.scenario_type in HYGIENE_SCENARIOS:
        return await _run_hygiene_case(
            client, case, base_url, top_k, run_id, trial
        )
    if case.scenario_type == "redistill_upgrade":
        return await _run_redistill_case(
            client, case, base_url, top_k, run_id, trial
        )
    if case.scenario_type == "l0_pipeline_consume":
        return await _run_l0_pipeline_case(
            client, case, base_url, top_k, run_id, trial
        )

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "scenario_type": case.scenario_type,
        "language": case.language,
        "question": case.query,
        "ground_truth": case.ground_truth,
        "trial": trial,
        "ragas_eligible": True,
    }
    try:
        _, background_write_ms = await post_json(
            client,
            f"{base_url}/v1/mems/write",
            {
                "user_id": case.user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "scope": case.scope,
                "messages": case.background_turns,
            },
        )
        # Simulate old background memories: backdate before distilling them to L2.
        backdate_agent_records(agent_id, days=settings.ARCHIVE_DAYS + 30)
        distill_result, distill_ms = await force_distill(agent_id)
        _, recent_write_ms = await post_json(
            client,
            f"{base_url}/v1/mems/write",
            {
                "user_id": case.user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "scope": case.scope,
                "messages": case.recent_turns,
            },
        )
        query_json, query_ms = await post_json(
            client,
            f"{base_url}/v1/mems/query",
            {
                "user_id": case.user_id,
                "agent_id": agent_id,
                "scope": case.scope,
                "query": case.query,
                "top_k": top_k,
            },
        )

        contexts = [item["content"] for item in query_json.get("results", [])]
        retrieved_sources = [item["source"] for item in query_json.get("results", [])]
        answer, answer_ms = await generate_answer(
            case.query,
            contexts,
            case.recent_turns,
            case.language,
            case.scenario_type,
            answer_config,
        )

        l1_hit, l1_rank = _hint_metrics(contexts, case.expected_l1_hint)
        l2_hit, l2_rank = _hint_metrics(contexts, case.expected_l2_hint)
        joined_contexts = " ".join(contexts).lower()
        conflict_resolved = 1 if (
            l1_hit
            and (
                not case.excluded_hint
                or case.excluded_hint.lower() not in joined_contexts
            )
        ) else 0

        notes: list[str] = []
        if case.expected_l1_hint and not l1_hit:
            notes.append("l1_hint_not_obvious")
        if case.expected_l2_hint and not l2_hit:
            notes.append("l2_hint_not_obvious")
        if case.language == "zh" and answer.isascii():
            notes.append("language_mismatch")
        if case.language == "en" and any("\u4e00" <= ch <= "\u9fff" for ch in answer):
            notes.append("language_mismatch")
        if not distill_result.get("success"):
            notes.append("distill_failed")

        result.update(
            {
                "status": "ok",
                "l1_hit": l1_hit,
                "l1_rank": l1_rank,
                "l1_rr": 1.0 / l1_rank if l1_hit else 0.0,
                "l2_hit": l2_hit,
                "l2_rank": l2_rank,
                "l2_rr": 1.0 / l2_rank if l2_hit else 0.0,
                "conflict_resolved": conflict_resolved,
                "write_ms": background_write_ms + recent_write_ms,
                "distill_ms": distill_ms,
                "query_ms": query_ms,
                "answer_ms": answer_ms,
                "answer": answer,
                "contexts": contexts,
                "retrieved_sources": retrieved_sources,
                "notes": ", ".join(notes),
            }
        )
    except Exception as exc:
        return _fail_result(case, trial, exc, started)
    finally:
        await cleanup_agent_data(agent_id, session_id, case.user_id, case.scope)

    result["end_to_end_ms"] = (time.perf_counter() - started) * 1000
    return result


def build_ragas_dataset(results: list[dict[str, Any]]) -> tuple[Dataset, list[int]]:
    row_indexes = [
        index
        for index, row in enumerate(results)
        if row.get("ragas_eligible", True)
        and row.get("status") == "ok"
        and row.get("contexts")
        and row.get("answer")
    ]
    valid_rows = [results[index] for index in row_indexes]
    dataset = Dataset.from_dict(
        {
            "user_input": [row["question"] for row in valid_rows],
            "response": [row["answer"] for row in valid_rows],
            "retrieved_contexts": [row["contexts"] for row in valid_rows],
            "reference": [row["ground_truth"] for row in valid_rows],
        }
    )
    return dataset, row_indexes


def parse_metric_names(raw: str) -> list[str]:
    names = [item.strip() for item in raw.split(",") if item.strip()]
    if not names:
        raise SystemExit("At least one metric must be provided via --metrics")
    allowed = {"faithfulness", "answer_relevance", "context_recall"}
    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise SystemExit(f"Unsupported metrics: {', '.join(unknown)}")
    return names


def _new_answer_relevance_metric() -> Any:
    """Create a more compatible answer-relevance metric instance.

    构造更保守、兼容性更好的 answer relevance 指标实例。
    """
    metric_cls = answer_relevance_metric.__class__
    return metric_cls(strictness=1)


def _apply_metric_scores(
    results: list[dict[str, Any]],
    row_indexes: list[int],
    metric_name: str,
    score_column: str,
    ragas_df: Any,
    ragas_ms: float,
) -> None:
    """Copy one metric result frame back into the case results.

    将单个指标的结果写回各 case 结果中。
    """
    per_metric_ms = ragas_ms / len(row_indexes) if row_indexes else 0.0
    for result_index, (_, score_row) in zip(
        row_indexes, ragas_df.iterrows(), strict=True
    ):
        value = score_row.get(score_column, float("nan"))
        if not math.isnan(float(value)):
            results[result_index][metric_name] = float(value)
        results[result_index]["ragas_ms"] = (
            results[result_index].get("ragas_ms", 0.0) + per_metric_ms
        )


def run_ragas(
    results: list[dict[str, Any]],
    ragas_config: EvaluatorConfig,
    *,
    metric_names: list[str],
    eval_limit: int | None,
) -> None:
    if eval_limit is not None:
        results = results[:eval_limit]
    dataset, row_indexes = build_ragas_dataset(results)
    if len(dataset) == 0:
        return

    llm = llm_factory(
        ragas_config.model,
        client=make_ragas_sync_client(ragas_config),
        temperature=0.0,
    )
    embeddings = OpenAICompatibleEmbeddings(
        model=ragas_config.embedding_model,
        api_key=ragas_config.api_key,
        base_url=ragas_config.base_url,
        timeout=ragas_config.timeout,
        dimensions=ragas_config.embedding_dimensions,
        embeddings_url=ragas_config.embeddings_url,
    )
    available_metric_specs = {
        "faithfulness": (faithfulness, "faithfulness", "faithfulness"),
        "answer_relevance": (
            _new_answer_relevance_metric(),
            "answer_relevance",
            "answer_relevancy",
        ),
        "context_recall": (context_recall, "context_recall", "context_recall"),
    }
    metric_specs = [available_metric_specs[name] for name in metric_names]

    print(
        f"Starting RAGAS evaluation on {len(dataset)} rows with metrics: {', '.join(metric_names)}",
        flush=True,
    )

    for metric, result_key, score_column in metric_specs:
        started = time.perf_counter()
        print(f"START metric={result_key} rows={len(dataset)}", flush=True)
        try:
            ragas_result = evaluate(
                dataset=dataset,
                metrics=[metric],
                llm=llm,
                embeddings=embeddings,
                raise_exceptions=False,
                show_progress=True,
            )
        except Exception as exc:
            print(f"ERROR metric={result_key} exc={exc!r}", flush=True)
            for result_index in row_indexes:
                metric_errors = results[result_index].setdefault("metric_errors", [])
                metric_errors.append(f"{result_key}: {exc}")
            continue

        ragas_ms = (time.perf_counter() - started) * 1000
        print(f"END metric={result_key} secs={ragas_ms / 1000:.2f}", flush=True)
        ragas_df = ragas_result.to_pandas()
        _apply_metric_scores(
            results,
            row_indexes,
            result_key,
            score_column,
            ragas_df,
            ragas_ms,
        )

    for result_index in row_indexes:
        metric_errors = results[result_index].pop("metric_errors", [])
        if metric_errors:
            existing_notes = results[result_index].get("notes", "")
            extra_notes = ", ".join(metric_errors)
            results[result_index]["notes"] = (
                f"{existing_notes}, {extra_notes}".strip(", ")
                if existing_notes
                else extra_notes
            )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Mems retrieval quality with RAGAS."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--results-path", default=str(DEFAULT_RESULTS_PATH))
    parser.add_argument("--metrics", default=DEFAULT_METRICS)
    parser.add_argument(
        "--eval-limit",
        type=int,
        default=0,
        help="Only evaluate the first N saved rows during RAGAS execution. 0 means all rows.",
    )
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="Skip Mems case execution and only run RAGAS on a saved results snapshot.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Run Mems benchmark cases and write the results snapshot without running RAGAS.",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Suffix for agent/session ids to avoid cross-run pollution (default: timestamp).",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Number of times to run each case (report shows mean±std).",
    )
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Include extended scenarios (re-distill upgrade, L0 output pipeline consumption).",
    )
    args = parser.parse_args()

    if args.trials < 1:
        raise SystemExit("--trials must be >= 1")

    answer_config = get_answer_llm_config()
    ragas_config = get_ragas_evaluator_config()
    results_path = Path(args.results_path)
    metric_names = parse_metric_names(args.metrics)
    eval_limit = args.eval_limit if args.eval_limit > 0 else None
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if not answer_config.api_key:
        raise SystemExit("OPENAI_API_KEY must be configured for answer generation.")
    if not ragas_config.api_key:
        raise SystemExit(
            "RAGAS_OPENAI_API_KEY or OPENAI_API_KEY must be configured for RAGAS evaluation."
        )

    # Preflight RAGAS evaluator connectivity only when RAGAS will actually run.
    if not args.prepare_only:
        preflight_client = make_ragas_sync_client(ragas_config)
        preflight_llm = preflight_client.chat.completions.create(
            model=ragas_config.model,
            messages=[{"role": "user", "content": "Reply with ok"}],
            temperature=0.0,
        )
        if not preflight_llm.choices or not preflight_llm.choices[0].message.content:
            raise SystemExit("RAGAS evaluator LLM preflight returned no content.")

        preflight_embeddings = OpenAICompatibleEmbeddings(
            model=ragas_config.embedding_model,
            api_key=ragas_config.api_key,
            base_url=ragas_config.base_url,
            timeout=ragas_config.timeout,
            dimensions=ragas_config.embedding_dimensions,
            embeddings_url=ragas_config.embeddings_url,
        )
        preflight_embeddings.embed_query("Reply with ok")

    if args.eval_only:
        if not results_path.exists():
            raise SystemExit(
                f"Results snapshot not found for eval-only mode: {results_path}"
            )
        results = read_results_snapshot(results_path)
        if args.prepare_only:
            raise SystemExit("--eval-only and --prepare-only cannot be combined.")
    else:
        cases = build_benchmark_cases(extended=args.extended)
        results: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            await wait_for_benchmark_dependencies(client, args.base_url)

            # Preflight answer-LLM connectivity.
            await generate_answer(
                question="Reply with ok.",
                contexts=["style response brevity prefers concise answers"],
                live_messages=[{"role": "user", "content": "test"}],
                language="en",
                scenario_type="implicit_preference",
                answer_config=answer_config,
            )

            for trial in range(1, args.trials + 1):
                trial_results: list[dict[str, Any]] = []
                for case in cases:
                    trial_results.append(
                        await run_case(
                            client,
                            case,
                            args.base_url,
                            args.top_k,
                            answer_config,
                            run_id=run_id,
                            trial=trial,
                        )
                    )
                if not args.prepare_only:
                    run_ragas(
                        trial_results,
                        ragas_config,
                        metric_names=metric_names,
                        eval_limit=eval_limit,
                    )
                results.extend(trial_results)

        write_results_snapshot(results, results_path)

    if args.prepare_only:
        print(f"Prepared results snapshot without RAGAS: {results_path}")
        return

    if args.eval_only:
        run_ragas(
            results,
            ragas_config,
            metric_names=metric_names,
            eval_limit=eval_limit,
        )
        write_results_snapshot(results, results_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = (
        Path(args.report_path)
        if args.report_path
        else REPORT_DIR / f"mems_ragas_report_{timestamp}.md"
    )
    build_report(
        results,
        report_path,
        args.base_url,
        args.top_k,
        answer_config,
        ragas_config,
    )
    print(
        f"Benchmark completed. Report written to {report_path}. Results snapshot: {results_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
