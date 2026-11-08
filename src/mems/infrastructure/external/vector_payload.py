"""Build Qdrant payloads for vector replicas.

向量副本的 payload 构造放在基础设施层，避免领域聚合产出特定存储的数据形状。
"""

from __future__ import annotations

from typing import Any

from mems.domain.memory import EpisodicMemory


def episodic_payload(memory: EpisodicMemory) -> dict[str, Any]:
    """Build the Qdrant payload for one L1 episodic vector replica."""
    return {
        "l1_id": memory.id,
        "vector_id": memory.vector_id,
        "tenant_id": memory.tenant_id,
        "user_id": memory.user_id,
        "agent_id": memory.agent_id,
        "session_id": memory.session_id,
        "namespace": memory.namespace,
        "content": memory.content,
    }


def summary_payload(
    *,
    tenant_id: str | None,
    user_id: str | None,
    agent_id: str,
    namespace: str | None,
    vector_id: str,
    content: str,
) -> dict[str, Any]:
    """Build the Qdrant payload for one L2 summary vector replica."""
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "namespace": namespace,
        "memory_type": "l2_summary",
        "vector_id": vector_id,
        "content": content,
    }
