"""Shared helpers for SQLModel repositories."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from mems.domain.identity import MemoryIdentity


class SqlRepository:
    """Base class for SQLModel repositories bound to one session."""

    def __init__(self, session: Session) -> None:
        self._session = session


def apply_identity_filter(query, model, identity: Optional[MemoryIdentity]):
    """Apply tenant/user/namespace filters to a SQLModel select statement."""
    if identity is None:
        return query
    if identity.tenant_id is not None:
        query = query.where(model.tenant_id == identity.tenant_id)
    if identity.user_id is not None:
        query = query.where(model.user_id == identity.user_id)
    if identity.namespace_key is not None:
        query = query.where(model.namespace == identity.namespace_key)
    return query
