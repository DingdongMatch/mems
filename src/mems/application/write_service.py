"""RememberMemory application service."""

from __future__ import annotations

import logging

from mems.application.commands import WriteMemoryCommand, WriteMemoryResult
from mems.config import settings
from mems.domain.identity import MemoryIdentity
from mems.domain.memory import EpisodicMemory
from mems.domain.outbox import OutboxEvent, OutboxEventType
from mems.domain.ports import (
    Clock,
    EpisodicRepository,
    OutboxRepository,
    UnitOfWork,
    WorkingMemoryStore,
)


logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 20


def _messages_to_content(messages: list[dict[str, str]]) -> str:
    """Flatten source messages into memory text."""
    return "\n".join(
        f"{message.get('role', 'user')}: {message.get('content', '')}"
        for message in messages
        if message.get("content")
    )


class WriteMemoryService:
    """Persist one memory into L0 and enqueue its durable L1 side effects."""

    def __init__(
        self,
        store: WorkingMemoryStore,
        episodic: EpisodicRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
        clock: Clock,
    ) -> None:
        self._store = store
        self._episodic = episodic
        self._outbox = outbox
        self._uow = uow
        self._clock = clock

    def _enqueue_distill_agent(self, agent_id: str) -> None:
        if self._outbox.exists_active(OutboxEventType.DISTILL_AGENT, agent_id):
            return
        self._outbox.add(
            OutboxEvent(
                event_type=OutboxEventType.DISTILL_AGENT,
                aggregate_id=agent_id,
                payload={
                    "agent_id": agent_id,
                    "batch_size": settings.DISTILL_IMMEDIATE_BATCH_SIZE,
                    "force": False,
                },
            )
        )

    def _enqueue_distill_due(self) -> None:
        aggregate_id = "global"
        if self._outbox.exists_active(OutboxEventType.DISTILL_DUE, aggregate_id):
            return
        self._outbox.add(
            OutboxEvent(
                event_type=OutboxEventType.DISTILL_DUE,
                aggregate_id=aggregate_id,
                payload={},
            )
        )

    async def remember(self, command: WriteMemoryCommand) -> WriteMemoryResult:
        new_messages = list(command.messages)
        memory_content = command.content or _messages_to_content(new_messages)
        identity = MemoryIdentity(
            tenant_id=command.tenant_id,
            user_id=command.user_id,
            agent_id=command.agent_id,
            session_id=command.session_id,
            namespace_segments=tuple(command.namespace),
        )

        l0 = await self._store.append_messages(
            tenant_id=command.tenant_id,
            user_id=command.user_id,
            agent_id=command.agent_id,
            session_id=command.session_id,
            messages=new_messages,
            content=memory_content,
            ttl_seconds=command.ttl_seconds,
            max_buffer_size=DEFAULT_CONTEXT_WINDOW,
            namespace=identity.namespace_key,
        )

        identity = l0.identity
        memory = EpisodicMemory.create(
            identity=identity,
            content=memory_content,
            messages=new_messages,
            importance_score=command.importance_score,
            now=self._clock.now(),
        )
        l1_id = self._episodic.add(memory)
        persisted = l1_id is not None

        if persisted:
            self._outbox.add(
                OutboxEvent(
                    event_type=OutboxEventType.VECTOR_SYNC,
                    aggregate_id=memory.vector_id,
                    payload={
                        "kind": "episodic",
                        "l1_id": memory.id,
                        "vector_id": memory.vector_id,
                        "content": memory_content,
                        "agent_id": identity.agent_id,
                        "tenant_id": identity.tenant_id,
                        "user_id": identity.user_id,
                        "session_id": identity.session_id,
                        "namespace": identity.namespace_key,
                    },
                )
            )
            if (
                command.importance_score
                >= settings.DISTILL_HIGH_IMPORTANCE_THRESHOLD
            ):
                self._enqueue_distill_agent(identity.agent_id)
            else:
                self._enqueue_distill_due()
            self._uow.commit()

        return WriteMemoryResult(
            success=True,
            tenant_id=l0.tenant_id,
            user_id=l0.user_id,
            agent_id=l0.agent_id,
            session_id=l0.session_id,
            namespace=command.namespace,
            importance_score=command.importance_score,
            content=memory_content,
            messages=new_messages,
            appended_count=len(new_messages),
            persisted_to_l1=persisted,
            l1_id=l1_id,
            message=(
                "Memory remembered and persisted to L1"
                if persisted
                else "Memory remembered in L0"
            ),
        )
