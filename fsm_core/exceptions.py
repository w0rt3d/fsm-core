"""
Exception hierarchy for fsm_core.

Everything the package raises inherits from CoreError, so callers who don't
care about the fine-grained type can do ``except CoreError`` once. Renamed
from the original ``exception.py`` -> ``exceptions.py`` to match the
conventional plural module name (stdlib does the same: ``exceptions``,
not ``exception``).
"""

__all__ = [
    "CoreError",
    "EventBusError",
    "InvalidEventNameError",
    "InvalidCallbackError",
    "InvalidPriorityError",
    "InvalidHandlerNameError",
    "InvalidLimitError",
    "HandlerNotFoundError",
    "GraphError",
    "InvalidOperationError",
    "DuplicateNodeError",
    "CycleDetectedError",
    "PluginError",
]


class CoreError(Exception):
    """Base class for every exception raised by fsm_core."""


# --- event bus -------------------------------------------------------------

class EventBusError(CoreError):
    """Base class for event-bus related errors."""


class InvalidEventNameError(EventBusError):
    pass


class InvalidCallbackError(EventBusError):
    pass


class InvalidPriorityError(EventBusError):
    pass


class InvalidHandlerNameError(EventBusError):
    pass


class InvalidLimitError(EventBusError):
    pass


class HandlerNotFoundError(EventBusError):
    pass


# --- graph -------------------------------------------------------------

class GraphError(CoreError):
    """Base class for node/graph related errors."""


class InvalidOperationError(GraphError):
    pass


class DuplicateNodeError(GraphError):
    pass


class CycleDetectedError(GraphError):
    pass


# --- plugins -------------------------------------------------------------

class PluginError(CoreError):
    pass
