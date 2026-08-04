from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Callable, Deque, Dict, List, Optional

from fsm_core._errors import wrap_errors
from fsm_core.context import Context
from fsm_core.exceptions import (
    EventBusError,
    HandlerNotFoundError,
    InvalidCallbackError,
    InvalidEventNameError,
    InvalidHandlerNameError,
    InvalidLimitError,
    InvalidPriorityError,
)

logger = logging.getLogger(__name__)


class EventPriority(IntEnum):
    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30
    LOWEST = 40


@dataclass
class Event:
    name: str
    data: Any
    context: Optional[Context]
    timestamp: datetime = field(default_factory=datetime.now)
    source: Optional[str] = None


@dataclass
class EventHandler:
    callback: Callable
    priority: EventPriority
    name: str


class _Validate:
    """Argument validation for EventBus, kept separate from the logic
    that uses it so each concern can be read (and tested) on its own."""

    @staticmethod
    def subscribe(
        event_name: str,
        callback: Callable,
        priority: EventPriority,
        name: str,
    ) -> None:
        if not isinstance(event_name, str):
            raise InvalidEventNameError("Event name must be a string")
        if not event_name.strip():
            raise InvalidEventNameError("Event name cannot be empty or whitespace")
        if not isinstance(name, str):
            raise InvalidHandlerNameError("Handler name must be a string")
        if name != "" and not name.strip():
            raise InvalidHandlerNameError("Handler name cannot be whitespace")
        if not callable(callback):
            raise InvalidCallbackError("Callback must be callable")
        if not isinstance(priority, EventPriority):
            raise InvalidPriorityError("Priority must be an EventPriority instance")

    @staticmethod
    def unsubscribe(event_name: str, name: str) -> None:
        if not isinstance(event_name, str):
            raise InvalidEventNameError("Event name must be a string")
        if not event_name.strip():
            raise InvalidEventNameError("Event name cannot be empty or whitespace")
        if not isinstance(name, str):
            raise InvalidHandlerNameError("Handler name must be a string")
        if not name.strip():
            raise InvalidHandlerNameError("Handler name cannot be empty or whitespace")

    @staticmethod
    def emit(event_name: str) -> None:
        if not isinstance(event_name, str):
            raise InvalidEventNameError("Event name must be a string")
        if not event_name.strip():
            raise InvalidEventNameError("Event name cannot be empty")

    @staticmethod
    def get_history(limit: Optional[int]) -> None:
        if limit is None:
            return
        if not isinstance(limit, int):
            raise InvalidLimitError("Limit must be an integer")
        if limit < 0:
            raise InvalidLimitError("Limit cannot be negative")


class EventBus:
    """
    Priority-ordered pub/sub with a bounded history ring buffer.

    Handlers subscribed with a lower ``EventPriority`` value run first
    (CRITICAL=0 ... LOWEST=40). A failing handler is logged and skipped;
    it never prevents the remaining handlers from running.
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = {}
        self._history: Deque[Event] = deque(maxlen=max_history)

    @wrap_errors(EventBusError, passthrough=(EventBusError,))
    def subscribe(
        self,
        event_name: str,
        callback: Callable,
        priority: EventPriority = EventPriority.NORMAL,
        name: str = "",
    ) -> "EventBus":
        _Validate.subscribe(event_name, callback, priority, name)
        handler_name = name.strip() or callback.__name__

        handlers = self._subscribers.setdefault(event_name, [])
        for existing in handlers:
            if existing.name == handler_name and existing.callback == callback:
                logger.warning("[EVENT_BUS] %s already subscribed to %s, skipping", handler_name, event_name)
                return self

        handler = EventHandler(callback=callback, name=handler_name, priority=priority)
        handlers.append(handler)
        handlers.sort(key=lambda h: h.priority)
        logger.info("[EVENT_BUS] %s subscribed to %s (priority=%s)", handler.name, event_name, priority.name)
        return self

    @wrap_errors(EventBusError, passthrough=(EventBusError,))
    def unsubscribe(self, event_name: str, name: str) -> "EventBus":
        _Validate.unsubscribe(event_name, name)

        handlers = self._subscribers.get(event_name)
        if not handlers:
            raise HandlerNotFoundError(f"No subscribers found for event '{event_name}'")

        remaining = [h for h in handlers if h.name != name]
        if len(remaining) == len(handlers):
            raise HandlerNotFoundError(f"Handler '{name}' not found for event '{event_name}'")

        if remaining:
            self._subscribers[event_name] = remaining
        else:
            del self._subscribers[event_name]

        logger.info("[EVENT_BUS] %s unsubscribed from %s", name, event_name)
        return self

    @wrap_errors(EventBusError, passthrough=(EventBusError,))
    def emit(self, event_name: str, data: Any, context: Optional[Context] = None) -> "EventBus":
        _Validate.emit(event_name)
        event = Event(name=event_name, data=data, context=context)
        self._history.append(event)

        handlers = self._subscribers.get(event_name, [])
        if not handlers:
            logger.debug("[EVENT_BUS] No handlers for event '%s'", event_name)
            return self

        success = errors = 0
        for handler in handlers:
            try:
                handler.callback(data, context)
                success += 1
            except Exception:
                errors += 1
                logger.error(
                    "[EVENT_BUS] Handler '%s' failed for '%s'", handler.name, event_name, exc_info=True
                )

        logger.debug(
            "[EVENT_BUS] Emitted '%s' (handlers=%d, success=%d, errors=%d)",
            event_name, len(handlers), success, errors,
        )
        return self

    @wrap_errors(EventBusError, passthrough=(EventBusError,))
    async def emit_async(self, event_name: str, data: Any, context: Optional[Context] = None) -> "EventBus":
        _Validate.emit(event_name)
        event = Event(name=event_name, data=data, context=context)
        self._history.append(event)

        handlers = self._subscribers.get(event_name, [])
        if not handlers:
            logger.debug("[EVENT_BUS] No handlers for event '%s'", event_name)
            return self

        async def run(h: EventHandler):
            if inspect.iscoroutinefunction(h.callback):
                return await h.callback(data, context)
            return await asyncio.to_thread(h.callback, data, context)

        results = await asyncio.gather(*(run(h) for h in handlers), return_exceptions=True)

        success = errors = 0
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                errors += 1
                logger.error("[EVENT_BUS] Handler '%s' failed: %s", handler.name, result, exc_info=result)
            else:
                success += 1

        logger.debug(
            "[EVENT_BUS] Async emitted '%s' (handlers=%d, success=%d, errors=%d)",
            event_name, len(handlers), success, errors,
        )
        return self

    @wrap_errors(EventBusError, passthrough=(EventBusError,))
    def get_history(self, limit: Optional[int] = None) -> List[Event]:
        _Validate.get_history(limit)
        history = list(self._history)
        if limit is None:
            return history
        # NOTE: the original was `history[-limit:] if limit <= len(history)
        # else history`. For limit == 0 that evaluates history[-0:], i.e.
        # history[0:] -- the *whole* history, not "give me nothing", which
        # is what a caller asking for limit=0 obviously means. Slicing
        # from the end explicitly avoids the -0 pitfall entirely.
        return history[len(history) - limit:] if limit else []

    def clear(self) -> "EventBus":
        self._history.clear()
        logger.debug("[EVENT_BUS] History cleared")
        return self

    def __repr__(self) -> str:
        return f"EventBus(events={sum(len(h) for h in self._subscribers.values())}, history={len(self._history)})"
