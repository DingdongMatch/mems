import logging
from pathlib import Path
from fastapi import FastAPI
from contextlib import asynccontextmanager

from mems.config import settings
from mems.database import init_db
from mems.routers import memories
from mems.services.scheduler import scheduler_service


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage, database, and scheduler around app lifetime.

    在应用生命周期前后初始化存储目录、数据库与调度器。
    """
    init_db()
    Path(settings.storage_l3_path).mkdir(parents=True, exist_ok=True)

    if settings.SCHEDULER_ENABLED:
        from mems.services.distill import trigger_distill_automatically
        from mems.services.archive import trigger_archive_automatically

        scheduler_service.add_distill_job(
            trigger_distill_automatically,
            hour=settings.DISTILL_CRON_HOUR,
            minute=settings.DISTILL_CRON_MINUTE,
        )
        scheduler_service.add_archive_job(
            trigger_archive_automatically,
            hour=settings.ARCHIVE_CRON_HOUR,
            minute=settings.ARCHIVE_CRON_MINUTE,
        )
        scheduler_service.start()
        logger.info("Scheduler started with distill and archive jobs")

    yield

    if settings.SCHEDULER_ENABLED:
        scheduler_service.shutdown()


app = FastAPI(
    title="Mems - Layered Memory System / 分层记忆系统",
    description=(
        "## Overview / 概述\n"
        "Mems is an industrial-grade memory hub solution for AI agents. Through a four-layer hot/cold-decoupled architecture, it provides a memory foundation with personality consistency, low-cost retrieval, and structured evolution.\n\n"
        "Mems 是一套定位工业级的 AI Agent 记忆中枢方案。它通过四层冷热解耦架构，为智能体提供具备人格一致性、低成本检索和结构化进化的记忆基座。\n\n"
        "## Core Design Philosophy / 核心设计哲学\n"
        "Unlike a single-layer vector database or basic RAG stack, Mems is designed to control memory entropy in long-running agents:\n\n"
        "- **⚡ Instant working memory (L0)**: Redis-based high-speed context cache that ensures millisecond-level response within the current session.\n"
        "- **🧠 Asynchronous knowledge distillation (L2)**: An automated LLM pipeline that extracts stable semantic facts from raw L1 narratives, enabling evolution from experience to cognition.\n"
        "- **🔍 Evidence grounding**: L2 distilled outputs stay strongly linked to the original L1 source material, avoiding black-box knowledge and keeping every inference traceable.\n"
        "- **📜 Century-scale archive (L3)**: A text-first strategy that turns ultra-long-term memory into persistent JSONL files, resisting hardware turnover and format obsolescence.\n"
        "- **🛡️ Production-grade isolation**: A built-in multi-tenant permission model across Tenant/User/Agent/Session, suitable for SaaS and enterprise agent deployments.\n"
        "- **🔌 Atomic integration**: An extremely compact API design where agent memory can be integrated in just three steps: `Context -> Query -> Write`.\n\n"
        "与传统的单层向量数据库（RAG）不同，Mems 解决了 Agent 在长期运行中的“信息熵增”问题：\n\n"
        "- **⚡ 瞬时工作记忆 (L0)**：基于 Redis 的极速上下文缓存，确保 Agent 在当前会话中具备毫秒级响应能力。\n"
        "- **🧠 异步知识蒸馏 (L2)**：自动化 LLM 流水线，从 L1 原始叙事中提炼**稳定的语义事实**，实现从“经历”到“认知”的进化。\n"
        "- **🔍 证据溯源 (Grounding)**：L2 提炼结果强关联 L1 原始语料，拒绝黑盒知识，确保 Agent 每一个推论都有据可查。\n"
        "- **📜 跨世纪归档 (L3)**：采用 **Text-First** 策略，将超长期记忆转化为 JSONL 持久化文件，对抗硬件更迭与格式淘汰。\n"
        "- **🛡️ 生产级隔离**：内建多租户（Tenant/User/Agent/Session）权限模型，完美适配 SaaS 与企业级 Agent 部署。\n"
        "- **🔌 原子化接入**：极致精简的 API 设计，只需 `Context -> Query -> Write` 三步即可完成 Agent 记忆赋能。\n\n"
        "## Public APIs / 公开接口\n"
        "1. `GET /v1/mems/context` fetches live context and paginated history.\n"
        "2. `POST /v1/mems/query` performs long-term recall across active L1 and L2.\n"
        "3. `POST /v1/mems/write` appends turns into the live session and persists them.\n"
        "4. `GET /v1/mems/status` shows system and pipeline status.\n"
        "5. `GET /v1/mems/health` is a lightweight health check.\n\n"
        "1. `GET /v1/mems/context` 获取 live 上下文和分页历史。\n"
        "2. `POST /v1/mems/query` 对活跃 L1 与 L2 做长期召回。\n"
        "3. `POST /v1/mems/write` 追加本轮消息并持久化。\n"
        "4. `GET /v1/mems/status` 查看系统和流水线状态。\n"
        "5. `GET /v1/mems/health` 做轻量健康检查。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(memories.router)


if __name__ == "__main__":
    import uvicorn

    Path(settings.storage_l3_path).mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "mems.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True
    )
