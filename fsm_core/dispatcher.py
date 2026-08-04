from __future__ import annotations

import functools
import logging
from typing import Any, Dict, Optional, Tuple

from fsm_core.context import Context
from fsm_core.event import EventBus
from fsm_core.plugins import PluginRegistry

logger = logging.getLogger(__name__)


# event name -> (hook name, ordered keys to pull out of event.data, whether
# to use call_hook_bool instead of the default call_hook)
#
# NOTE on the two entries marked below: the original dispatcher had a
# subtle bug here. GraphEngine.remove_node() emits "node_removed" with a
# {"node": ...} payload (no "pipeline" key at all), but the dispatcher
# required a non-None "pipeline" before calling the hook -- so
# on_node_removed silently never fired. Likewise "node_moved" was emitted
# with an "old_parents" *list* key (a node can have more than one parent
# in a DAG), but the dispatcher looked for "old_parent" (singular) --
# another silent no-op. Both the emitted keys (see engine.py) and the
# keys read here were renamed to agree with each other and with the fact
# that this is a DAG, not a tree.
_HOOK_TABLE: Dict[str, Tuple[str, Tuple[str, ...], bool]] = {
    "node_created": ("on_node_created", ("node",), False),
    "node_added": ("on_node_added", ("pipeline", "node", "index"), False),
    "node_removed": ("on_node_removed", ("node", "parents"), False),                # was: pipeline, node
    "node_moved": ("on_node_moved", ("node", "old_parents", "new_parent"), False),  # was: old_parent
    "node_changed": ("on_node_changed", ("node", "old_state", "new_state"), False),
    "pipeline_created": ("on_pipeline_created", ("pipeline",), False),
    "pipeline_validated": ("on_pipeline_validated", ("pipeline",), True),
    "pipeline_executed": ("on_pipeline_executed", ("pipeline",), False),
    "pipeline_mutated": ("on_pipeline_mutated", ("pipeline",), False),
    "graph_changed": ("on_graph_changed", ("pipeline",), False),
    "cycle_detected": ("on_cycle_detected", ("pipeline", "cycle_nodes"), False),
    "orphan_detected": ("on_orphan_detected", ("node",), False),
}

# Keys allowed to be legitimately absent/None (e.g. `index` when a node is
# appended rather than inserted at a position). Any other missing key
# means the event is malformed; it's logged and skipped rather than
# forwarded to plugins with a silently-wrong None.
_OPTIONAL_KEYS = {"index"}


class EventDispatcher:
    """
    Bridges EventBus events to PluginRegistry hook calls.

    This used to be twelve ~6-line methods, each pulling a couple of keys
    out of ``event.data`` and forwarding them to one specific hook -- all
    identical in shape, easy to get subtly wrong (see the bug noted on
    _HOOK_TABLE above), and tedious to extend for a new event type. It's
    now one table plus one generic dispatch method.
    """

    def __init__(self, event_bus: EventBus, registry: PluginRegistry) -> None:
        self._event_bus = event_bus
        self._registry = registry
        self._register_all_hooks()

    def _register_all_hooks(self) -> None:
        for event_name, (hook_name, keys, use_bool) in _HOOK_TABLE.items():
            handler = functools.partial(self._dispatch, event_name, hook_name, keys, use_bool)
            self._event_bus.subscribe(event_name, handler, name=f"dispatcher_{event_name}")

        self._event_bus.subscribe("plugin_error", self._handle_plugin_error, name="dispatcher_plugin_error")
        logger.debug("[DISPATCHER] Registered %d hooks", len(_HOOK_TABLE))

    def _dispatch(
        self,
        event_name: str,
        hook_name: str,
        keys: Tuple[str, ...],
        use_bool: bool,
        data: Any,
        context: Optional[Context],
    ) -> None:
        data = data or {}
        values = []
        for key in keys:
            value = data.get(key)
            if value is None and key not in _OPTIONAL_KEYS:
                logger.warning(
                    "[DISPATCHER] Event '%s' missing required key '%s', skipping %s",
                    event_name, key, hook_name,
                )
                return
            values.append(value)

        if use_bool:
            self._registry.call_hook_bool(hook_name, *values, context)
        else:
            self._registry.call_hook(hook_name, *values, context)

    def _handle_plugin_error(self, data: Any, context: Optional[Context]) -> None:
        data = data or {}
        plugin_name = data.get("plugin_name")
        error = data.get("error")
        if plugin_name is not None and error is not None:
            logger.error("[DISPATCHER] Plugin %s error: %s", plugin_name, error)
