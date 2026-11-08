"""Pure helpers for re-distilling L3 archives into L2.

再蒸馏的纯函数辅助；具体装配与编排在组合根（interfaces.bootstrap）中完成。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict


def content_fingerprint(record: Dict[str, Any]) -> str:
    """Stable fingerprint of identity + content + messages."""
    identity = {
        "tenant_id": record.get("tenant_id"),
        "user_id": record.get("user_id"),
        "agent_id": record.get("agent_id"),
    }
    identity["namespace"] = record.get("namespace")
    canonical = json.dumps(
        {
            **identity,
            "content": record.get("content"),
            "messages": record.get("messages"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _record_created_at(record: Dict[str, Any]) -> datetime:
    raw = record.get("created_at")
    if isinstance(raw, str):
        try:
            return _as_utc(datetime.fromisoformat(raw))
        except ValueError:
            pass
    return datetime.now(timezone.utc)
