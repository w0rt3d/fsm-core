from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class Context(BaseModel):
    """
    Ambient information carried alongside an operation (who triggered it,
    which transaction/session it belongs to, arbitrary metadata).

    Passed through to every event and every plugin hook so plugins can
    make decisions based on *who* changed the graph, not just *what*
    changed.
    """

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    transaction_id: Optional[str] = None
    source: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    # NOTE: this used to have no default, which made ``Context()`` raise a
    # pydantic ValidationError -- every caller was forced to pass
    # metadata={} explicitly. Defaulting it to {} is what every other
    # dict field in this codebase already does.
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def __repr__(self) -> str:
        user = self.user_id or "anonymous"
        # NOTE: the original code sliced session_id[8:] ("everything after
        # the first 8 characters"), which is backwards for a short
        # preview -- it also happens to skip the most distinguishing
        # part of a uuid4. session_id[:8] is what was intended.
        return (
            f"Context(user={user!r}, source={self.source!r}, "
            f"session={self.session_id[:8]}...)"
        )


class ContextManager:
    """Tracks the single "current" context for the calling thread/flow."""

    def __init__(self) -> None:
        self._current_context: Optional[Context] = None

    def create(self, **kwargs: Any) -> Context:
        self._current_context = Context(**kwargs)
        return self._current_context

    def get_current(self) -> Optional[Context]:
        return self._current_context

    def set_current(self, context: Context) -> None:
        self._current_context = context

    def clear(self) -> None:
        self._current_context = None

    def has_current(self) -> bool:
        return self._current_context is not None
