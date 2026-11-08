import logging
import asyncio
from pathlib import Path
from fastapi import FastAPI
from contextlib import asynccontextmanager

from mems.config import settings
from mems.infrastructure.persistence.database import init_db
from mems.interfaces.api import memories
from mems.interfaces.bootstrap import build_outbox_worker
from mems.infrastructure.external.scheduler import scheduler_service
from mems.infrastructure.external.observability import (
    RequestContextMiddleware,
    configure_logging,
)


logger = logging.getLogger(__name__)
configure_logging(settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage, database, and scheduler around app lifetime.

    在应用生命周期前后初始化存储目录、数据库与调度器。
    """
    init_db()
    Path(settings.storage_l3_path).mkdir(parents=True, exist_ok=True)

    worker_task = None
    if settings.OUTBOX_WORKER_ENABLED:
        worker_task = asyncio.create_task(build_outbox_worker().run_forever())

    if settings.SCHEDULER_ENABLED:
        from mems.interfaces.bootstrap import trigger_archive_automatically

        scheduler_service.add_archive_job(
            trigger_archive_automatically,
            hour=settings.ARCHIVE_CRON_HOUR,
            minute=settings.ARCHIVE_CRON_MINUTE,
        )
        scheduler_service.start()
        logger.info(
            "Scheduler started with archive job (distill is threshold-triggered)"
        )

    yield

    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    if settings.SCHEDULER_ENABLED:
        scheduler_service.shutdown()


app = FastAPI(
    title="Mems - Layered Memory System / 分层记忆系统",
    description=(
        "## Overview / 概述\n"
        "Mems is an industrial-grade memory infrastructure for AI agents. Through a four-layer hot/cold-decoupled architecture, it provides a memory foundation with personality consistency, low-cost retrieval, and structured evolution.\n\n"
        "Mems 是一套定位工业级的 AI Agent 记忆基础设施。它通过四层冷热解耦架构，为智能体提供具备人格一致性、低成本检索和结构化进化的记忆基座。\n\n"
        "## Core Design Philosophy / 核心设计哲学\n"
        "Unlike a single-layer vector database or basic RAG stack, Mems is designed to control memory entropy in long-running agents:\n\n"
        "- **⚡ Hot memory cache (L0)**: Redis-backed recent-memory cache for fast recall without owning agent runtime state.\n"
        "- **🧠 Asynchronous knowledge distillation (L2)**: An automated LLM pipeline that extracts stable semantic facts from raw L1 narratives, enabling evolution from experience to cognition.\n"
        "- **🔍 Evidence grounding**: L2 distilled outputs stay strongly linked to the original L1 source material, avoiding black-box knowledge and keeping every inference traceable.\n"
        "- **📜 Century-scale archive (L3)**: A text-first strategy that turns ultra-long-term memory into persistent JSONL files, resisting hardware turnover and format obsolescence.\n"
        "- **🛡️ Consistent memory partitioning**: Tenant/User/Agent identity and caller-provided namespace context are applied across storage adapters; business authorization stays in the caller application.\n"
        "- **🔌 Atomic integration**: An extremely compact API design centered on `Remember -> Recall`.\n\n"
        "与传统的单层向量数据库（RAG）不同，Mems 解决了 Agent 在长期运行中的“信息熵增”问题：\n\n"
        "- **⚡ 热记忆缓存 (L0)**：基于 Redis 的近期记忆缓存，用于快速召回，但不接管 Agent 运行时状态。\n"
        "- **🧠 异步知识蒸馏 (L2)**：自动化 LLM 流水线，从 L1 原始叙事中提炼**稳定的语义事实**，实现从“经历”到“认知”的进化。\n"
        "- **🔍 证据溯源 (Grounding)**：L2 提炼结果强关联 L1 原始语料，拒绝黑盒知识，确保 Agent 每一个推论都有据可查。\n"
        "- **📜 跨世纪归档 (L3)**：采用 **Text-First** 策略，将超长期记忆转化为 JSONL 持久化文件，对抗硬件更迭与格式淘汰。\n"
        "- **🛡️ 一致的记忆分区**：Tenant/User/Agent identity 与调用方提供的 namespace 上下文在各存储适配器中保持一致；业务授权由调用方应用负责。\n"
        "- **🔌 原子化接入**：围绕 `Remember -> Recall` 的极简 API 设计。\n\n"
        "## Public APIs / 公开接口\n"
        "1. `POST /v1/mems/write` remembers memory content into L0/L1.\n"
        "2. `POST /v1/mems/query` recalls relevant memories across L0, L1, and L2.\n"
        "3. `POST /v1/mems/redistill` re-distills archived L3 records into L2 with the current model.\n"
        "4. `GET /v1/mems/status` shows system and pipeline status.\n"
        "5. `GET /v1/mems/health` is a lightweight health check.\n\n"
        "1. `POST /v1/mems/write` 将记忆内容写入 L0/L1。\n"
        "2. `POST /v1/mems/query` 跨 L0、L1、L2 召回相关记忆。\n"
        "3. `POST /v1/mems/redistill` 用当前模型将归档 L3 记录再蒸馏到 L2。\n"
        "4. `GET /v1/mems/status` 查看系统和流水线状态。\n"
        "5. `GET /v1/mems/health` 做轻量健康检查。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestContextMiddleware)

app.include_router(memories.router)


if __name__ == "__main__":
    import uvicorn

    Path(settings.storage_l3_path).mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "mems.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True
    )
