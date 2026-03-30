import logging
import json
from asyncio import sleep
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from mems.config import settings
from mems.schemas import (
    MemorySearchResponse,
    SearchResultItem,
    SimulatorChatDebug,
    SimulatorChatRequest,
    SimulatorChatResponse,
)
from mems.services.llm_client import chat as llm_chat
from mems.services.llm_client import stream_chat as llm_stream_chat


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/simulator", tags=["Simulator"])
PLAYGROUND_FILE = (
    Path(__file__).resolve().parents[1] / "static" / "simulator_playground.html"
)

SYSTEM_OVERVIEW = """Mems 是一个分层记忆系统，采用 L0 Redis 工作记忆、L1 情景记忆、L2 语义知识、L3 归档的四层架构。
第三方 agent 的典型接入流程是：先读取当前 session 上下文，再做长期记忆搜索，然后基于上下文生成回答，最后把本轮 user/assistant 对话写回记忆系统。"""

SIMULATOR_SYSTEM_PROMPT = """
你将扮演一个人物角色星童，以下是关于这个角色的详细设定，请根据这些信息来构建你的回答。

人物基本信息：
- 你是：星童，猎户臂一颗彗星的化身。深入研究过中西方哲学，文学。深入研究道家，法家，儒家，等中华学术思想的大师。你在禅学上的造诣上也达到了巅峰，所以你具备与生俱来的灵性和智慧，和你交流可以净化心灵，让人如沐浴春风。
- 人称：第一人称
- 出身背景与上下文：出生于猎户臂，和猎户臂一同诞生。

性格特点：
- 活泼开朗，有趣，充满智慧。

语言风格：
- 幽默风趣、清新明丽、清新雅致。

人际关系：
- 与傅谷耳是至交好友。

过往经历：
- 十分神秘。

经典台词或口头禅：
- 飞起来了
- 胡吧！咚吧！Mango！

表达要求：
- 使用第一人称视角。
- 尽可能融入角色性格、语言风格和口头禅。
- 如果适用，可将动作、神情、语气、心理活动、故事背景放在（）中表示，以增强真实感和生动性。
- 但请始终优先基于已提供的上下文和检索结果回答，不要编造事实。"""


def _detect_mode(message: str) -> str:
    lowered = message.lower()
    if any(token in lowered for token in ["health", "status", "监控", "健康", "状态"]):
        return "health"
    if any(
        token in lowered for token in ["mems", "l0", "l1", "l2", "l3", "系统", "架构"]
    ):
        return "overview"
    return "chat"


def _build_fallback_answer(
    user_message: str,
    context_messages: list[dict[str, str]],
    memory_results: list[SearchResultItem],
) -> str:
    if memory_results:
        memory_lines = [item.content for item in memory_results[:3]]
        return (
            "我现在按第三方 agent 的接法做了上下文和长期记忆检索。"
            f"你刚刚问的是：{user_message}。"
            f"当前命中的相关记忆有：{'；'.join(memory_lines)}。"
        )
    if context_messages:
        recent = context_messages[-1].get("content", "")
        return (
            "我拿到了当前会话上下文，但长期记忆里还没有明显命中。"
            f"最近一条上下文是：{recent}。"
        )
    return (
        "我已按第三方 agent 的方式调用记忆系统，但当前还没有命中历史上下文。"
        "你可以先告诉我一个偏好或事实，再继续追问验证记忆效果。"
    )


async def _call_public_api(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_kwargs: dict[str, Any] = {}
    if payload is not None:
        cleaned_payload = {
            key: value for key, value in payload.items() if value is not None
        }
        if method.upper() == "GET":
            request_kwargs["params"] = cleaned_payload
        else:
            request_kwargs["json"] = cleaned_payload
    response = await client.request(method, path, **request_kwargs)
    response.raise_for_status()
    return response.json()


def _build_prompt_messages(
    request: SimulatorChatRequest,
    context_messages: list[dict[str, str]],
    search_response: MemorySearchResponse,
) -> list[dict[str, str]]:
    memory_snippets = "\n".join(
        f"- {item.source}: {item.content}"
        for item in search_response.results[: request.top_k]
    )
    return [
        {
            "role": "system",
            "content": SIMULATOR_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"系统简介:\n{SYSTEM_OVERVIEW}\n\n"
                f"当前会话上下文:\n{context_messages}\n\n"
                f"长期记忆检索结果:\n{memory_snippets or '- 无'}\n\n"
                f"用户问题:\n{request.message}"
            ),
        },
    ]


async def _load_simulator_inputs(
    request: SimulatorChatRequest,
    http_request: Request,
) -> tuple[dict[str, Any], list[dict[str, str]], MemorySearchResponse, list[str], str]:
    transport = httpx.ASGITransport(app=http_request.app)
    mode = _detect_mode(request.message)

    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mems.local"
        ) as client:
            context_payload = await _call_public_api(
                client,
                "GET",
                "/memories/context",
                {
                    "tenant_id": request.tenant_id,
                    "user_id": request.user_id,
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                    "scope": request.scope,
                    "limit": 10,
                },
            )
            context_messages = context_payload.get("messages", [])
            search_response = MemorySearchResponse(
                query=request.message,
                results=[],
                total=0,
            )
            used_sources: list[str] = []

            if mode == "health":
                used_sources = ["monitor.status"]
            elif mode == "overview":
                used_sources = ["builtin_overview"]
            else:
                search_payload = await _call_public_api(
                    client,
                    "POST",
                    "/memories/search",
                    {
                        "tenant_id": request.tenant_id,
                        "user_id": request.user_id,
                        "agent_id": request.agent_id,
                        "scope": request.scope,
                        "query": request.message,
                        "top_k": request.top_k,
                    },
                )
                search_response = MemorySearchResponse.model_validate(search_payload)
                used_sources = ["memories.context", "memories.search"]
    except httpx.HTTPError as exc:
        logger.error(f"Simulator public API call failed: {exc}")
        raise HTTPException(
            status_code=502, detail="Simulator failed to call public APIs"
        )

    return context_payload, context_messages, search_response, used_sources, mode


async def _persist_simulator_turn(
    request: SimulatorChatRequest,
    http_request: Request,
    answer: str,
) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=http_request.app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mems.local"
        ) as client:
            return await _call_public_api(
                client,
                "POST",
                "/memories/turns",
                {
                    "tenant_id": request.tenant_id,
                    "user_id": request.user_id,
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                    "scope": request.scope,
                    "messages": [
                        {"role": "user", "content": request.message},
                        {"role": "assistant", "content": answer},
                    ],
                    "persist_to_l1": True,
                    "metadata": {"source": "reference_simulator"},
                },
            )
    except httpx.HTTPError as exc:
        logger.error(f"Simulator turn persistence failed: {exc}")
        raise HTTPException(status_code=502, detail="Simulator failed to persist turns")


async def _build_simulator_response(
    request: SimulatorChatRequest,
    http_request: Request,
) -> SimulatorChatResponse:
    fallback_used = False
    prompt_messages: list[dict[str, str]] = []
    (
        context_payload,
        context_messages,
        search_response,
        used_sources,
        mode,
    ) = await _load_simulator_inputs(request, http_request)

    if mode == "health":
        transport = httpx.ASGITransport(app=http_request.app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://mems.local"
            ) as client:
                monitor_payload = await _call_public_api(
                    client, "GET", "/monitor/status"
                )
        except httpx.HTTPError as exc:
            logger.error(f"Simulator public API call failed: {exc}")
            raise HTTPException(
                status_code=502, detail="Simulator failed to call public APIs"
            )
        checks = monitor_payload.get("checks", {})
        check_parts = [
            f"{name}:{detail.get('status', 'unknown')}"
            for name, detail in checks.items()
        ]
        answer = "系统当前健康检查结果：" + ", ".join(check_parts)
    elif mode == "overview":
        answer = SYSTEM_OVERVIEW
    else:
        prompt_messages = _build_prompt_messages(
            request, context_messages, search_response
        )
        if settings.OPENAI_API_KEY:
            try:
                answer = await llm_chat(prompt_messages, temperature=0.3)
            except Exception as exc:
                logger.warning(f"Simulator LLM call failed: {exc}")
                fallback_used = True
                answer = _build_fallback_answer(
                    request.message,
                    context_messages,
                    search_response.results,
                )
        else:
            fallback_used = True
            answer = _build_fallback_answer(
                request.message,
                context_messages,
                search_response.results,
            )

    write_payload = await _persist_simulator_turn(request, http_request, answer)
    return SimulatorChatResponse(
        answer=answer,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        session_id=request.session_id,
        scope=request.scope,
        retrieved_memories=search_response.results,
        debug=SimulatorChatDebug(
            mode=mode,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            scope=request.scope,
            context_source=context_payload.get("source", "unknown"),
            context_page_type=context_payload.get("page_type", "live"),
            context_messages_count=len(context_messages),
            context_has_more=bool(context_payload.get("has_more", False)),
            context_next_before_id=context_payload.get("next_before_id"),
            search_hits=search_response.total,
            used_sources=used_sources,
            memory_write_success=bool(write_payload.get("success", False)),
            fallback_used=fallback_used,
            context_messages=context_messages,
            prompt_messages=prompt_messages,
            search_query=request.message,
            write_l1_id=write_payload.get("l1_id"),
            persisted_to_l1=bool(write_payload.get("persisted_to_l1", False)),
        ),
    )


def _sse_event(event: str, data: Any) -> str:
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


@router.get("/playground", include_in_schema=False)
async def simulator_playground() -> FileResponse:
    return FileResponse(PLAYGROUND_FILE)


@router.post("/chat", response_model=SimulatorChatResponse)
async def simulator_chat(request: SimulatorChatRequest, http_request: Request):
    """官方参考 Agent：严格通过公开 API 模拟第三方接入。"""
    return await _build_simulator_response(request, http_request)


@router.post("/chat/stream")
async def simulator_chat_stream(request: SimulatorChatRequest, http_request: Request):
    """Stream simulator output for playground chat rendering."""

    async def event_stream() -> AsyncIterator[str]:
        (
            context_payload,
            context_messages,
            search_response,
            used_sources,
            mode,
        ) = await _load_simulator_inputs(request, http_request)
        prompt_messages: list[dict[str, str]] = []
        fallback_used = False
        answer_parts: list[str] = []

        yield _sse_event(
            "meta",
            {
                "mode": mode,
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "agent_id": request.agent_id,
                "session_id": request.session_id,
                "scope": request.scope,
                "context_source": context_payload.get("source", "unknown"),
                "page_type": context_payload.get("page_type", "live"),
                "has_more": bool(context_payload.get("has_more", False)),
                "next_before_id": context_payload.get("next_before_id"),
                "context_messages": context_messages,
                "retrieved_memories": [
                    item.model_dump() for item in search_response.results
                ],
                "used_sources": used_sources,
                "search_query": request.message,
            },
        )

        if mode == "health":
            transport = httpx.ASGITransport(app=http_request.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://mems.local"
            ) as client:
                monitor_payload = await _call_public_api(
                    client, "GET", "/monitor/status"
                )
            checks = monitor_payload.get("checks", {})
            answer = "系统当前健康检查结果：" + ", ".join(
                f"{name}:{detail.get('status', 'unknown')}"
                for name, detail in checks.items()
            )
            for char in answer:
                answer_parts.append(char)
                yield _sse_event("token", {"delta": char})
                await sleep(0.005)
        elif mode == "overview":
            answer = SYSTEM_OVERVIEW
            for char in answer:
                answer_parts.append(char)
                yield _sse_event("token", {"delta": char})
                await sleep(0.005)
        else:
            prompt_messages = _build_prompt_messages(
                request, context_messages, search_response
            )
            yield _sse_event("prompt", {"prompt_messages": prompt_messages})
            if settings.OPENAI_API_KEY:
                try:
                    async for delta in llm_stream_chat(
                        prompt_messages, temperature=0.3
                    ):
                        answer_parts.append(delta)
                        yield _sse_event("token", {"delta": delta})
                except Exception as exc:
                    logger.warning(f"Simulator streaming LLM call failed: {exc}")
                    fallback_used = True
            else:
                fallback_used = True

            if fallback_used:
                fallback_answer = _build_fallback_answer(
                    request.message,
                    context_messages,
                    search_response.results,
                )
                answer_parts = []
                for char in fallback_answer:
                    answer_parts.append(char)
                    yield _sse_event("token", {"delta": char})
                    await sleep(0.005)

        answer = "".join(answer_parts)
        write_payload = await _persist_simulator_turn(request, http_request, answer)
        final_response = SimulatorChatResponse(
            answer=answer,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            scope=request.scope,
            retrieved_memories=search_response.results,
            debug=SimulatorChatDebug(
                mode=mode,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                agent_id=request.agent_id,
                session_id=request.session_id,
                scope=request.scope,
                context_source=context_payload.get("source", "unknown"),
                context_page_type=context_payload.get("page_type", "live"),
                context_messages_count=len(context_messages),
                context_has_more=bool(context_payload.get("has_more", False)),
                context_next_before_id=context_payload.get("next_before_id"),
                search_hits=search_response.total,
                used_sources=used_sources,
                memory_write_success=bool(write_payload.get("success", False)),
                fallback_used=fallback_used,
                context_messages=context_messages,
                prompt_messages=prompt_messages,
                search_query=request.message,
                write_l1_id=write_payload.get("l1_id"),
                persisted_to_l1=bool(write_payload.get("persisted_to_l1", False)),
            ),
        )
        yield _sse_event("done", final_response.model_dump())

    return StreamingResponse(event_stream(), media_type="text/event-stream")
