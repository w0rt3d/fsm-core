from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional

from fsm_core._errors import wrap_errors
from fsm_core.exceptions import PluginError

if TYPE_CHECKING:
    from fsm_core.context import Context
    from fsm_core.node import BaseNode, BasePipeline

logger = logging.getLogger(__name__)


class PluginHealth:
    """Simple circuit breaker: after ``max_errors`` consecutive failed
    hook calls, the plugin is quarantined and skipped until reset."""

    def __init__(self, max_errors: int = 3) -> None:
        self.error_count: int = 0
        self.max_errors: int = max_errors
        self.is_quarantined: bool = False
        self.quarantine_reason: Optional[str] = None
        self.last_error: Optional[Exception] = None
        self.success_count: int = 0

    def record_success(self) -> None:
        self.success_count += 1

    def record_error(self, error: Exception) -> None:
        self.error_count += 1
        self.last_error = error
        if self.error_count >= self.max_errors:
            self.is_quarantined = True
            self.quarantine_reason = f"Too many errors: {self.error_count}/{self.max_errors}. Last: {error}"

    def reset(self) -> None:
        self.__init__(self.max_errors)  # type: ignore[misc]


class BasePlugin(ABC):
    """
    Extend this and override the ``on_*`` hooks you care about.

    Hook signatures use plural ``parents`` (not a single ``pipeline``)
    because this is a DAG: a node can have more than one parent. See
    CHANGES.md for why this differs from the first version of the hooks.
    """

    def __init__(self) -> None:
        self._health = PluginHealth()

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @property
    def priority(self) -> int:
        return 100

    @property
    def enabled(self) -> bool:
        return True

    def is_quarantined(self) -> bool:
        return self._health.is_quarantined

    def error_count(self) -> int:
        return self._health.error_count

    def reset_health(self) -> None:
        self._health.reset()

    # -- lifecycle -----------------------------------------------------
    def on_register(self, registry: "PluginRegistry") -> None: ...
    def on_ready(self) -> None: ...
    def on_unregister(self) -> None: ...

    # -- graph events ----------------------------------------------
    def on_node_created(self, node: "BaseNode", context: Optional["Context"]) -> None: ...
    def on_node_added(self, pipeline: "BasePipeline", node: "BaseNode", index: Optional[int], context: Optional["Context"]) -> None: ...
    def on_node_removed(self, node: "BaseNode", parents: List["BaseNode"], context: Optional["Context"]) -> None: ...
    def on_node_moved(self, node: "BaseNode", old_parents: List["BaseNode"], new_parent: "BaseNode", context: Optional["Context"]) -> None: ...
    def on_node_changed(self, node: "BaseNode", old_state: Dict[str, Any], new_state: Dict[str, Any], context: Optional["Context"]) -> None: ...
    def on_pipeline_created(self, pipeline: "BasePipeline", context: Optional["Context"]) -> None: ...
    def on_pipeline_validated(self, pipeline: "BasePipeline", context: Optional["Context"]) -> Optional[bool]:
        return None
    def on_pipeline_executed(self, pipeline: "BasePipeline", context: Optional["Context"]) -> None: ...
    def on_pipeline_mutated(self, pipeline: "BasePipeline", context: Optional["Context"]) -> None: ...
    def on_graph_changed(self, pipeline: "BasePipeline", context: Optional["Context"]) -> None: ...
    def on_cycle_detected(self, pipeline: "BasePipeline", cycle_nodes: List["BaseNode"], context: Optional["Context"]) -> None: ...
    def on_orphan_detected(self, node: "BaseNode", context: Optional["Context"]) -> None: ...

    # -- extensibility -----------------------------------------------
    def get_extensions(self) -> Dict[str, Callable]:
        return {}

    def get_dependencies(self) -> List[str]:
        return []

    def get_state(self) -> Dict[str, Any]:
        return {}

    def set_state(self, state: Dict[str, Any]) -> None: ...

    def _record_success(self) -> None:
        self._health.record_success()

    def _record_error(self, error: Exception) -> None:
        self._health.record_error(error)


class PluginRegistry:
    """
    Holds registered plugins and dispatches hook calls to them in
    priority order, skipping quarantined/disabled plugins.

    ``call_hook`` / ``call_hook_first`` / ``call_hook_bool`` used to be
    three ~15-line copies of the same loop with a different exit
    condition. They're now three thin wrappers around one shared
    iterator + a strategy callback, so the "skip quarantined/disabled,
    catch and record errors" logic only exists once.
    """

    def __init__(self) -> None:
        self._plugins: Dict[str, BasePlugin] = {}
        self._extensions: Dict[str, Callable] = {}
        self._is_ready: bool = False
        self._quarantined: List[str] = []

    @wrap_errors(PluginError)
    def register(self, plugin: BasePlugin) -> "PluginRegistry":
        if plugin.name in self._plugins:
            logger.warning("[REGISTRY] Plugin %s already registered, skipping", plugin.name)
            return self

        self._plugins[plugin.name] = plugin
        plugin.on_register(self)

        for ext_name, ext in plugin.get_extensions().items():
            self._extensions[ext_name] = ext

        logger.info("[REGISTRY] Plugin %s v%s registered", plugin.name, plugin.version)
        return self

    def ready(self) -> None:
        self._is_ready = True
        for plugin in self._plugins.values():
            try:
                plugin.on_ready()
                logger.debug("[REGISTRY] Plugin %s is ready", plugin.name)
            except Exception as e:
                logger.error("[REGISTRY] Plugin %s on_ready failed: %s", plugin.name, e)

    def get_plugin(self, name: str) -> Optional[BasePlugin]:
        return self._plugins.get(name)

    def get_extension(self, name: str) -> Optional[Callable]:
        return self._extensions.get(name)

    def get_all_plugins(self) -> List[BasePlugin]:
        return list(self._plugins.values())

    def get_plugins_by_priority(self) -> List[BasePlugin]:
        return sorted(self._plugins.values(), key=lambda p: p.priority)

    def _active_plugins(self) -> Iterator[BasePlugin]:
        for plugin in self.get_plugins_by_priority():
            if plugin.is_quarantined() or not plugin.enabled:
                continue
            yield plugin

    def _call(self, plugin: BasePlugin, method: Callable, args: tuple) -> Any:
        """Invoke one already-resolved hook method, recording success/
        failure on the plugin's health tracker. Raises on failure so
        callers can decide whether to skip or abort; callers here always
        catch it and move on to the next plugin."""
        try:
            result = method(*args)
            plugin._record_success()
            return result
        except Exception as e:
            plugin._record_error(e)
            logger.error("[REGISTRY] Hook %s failed for plugin %s: %s", method.__name__, plugin.name, e)
            if plugin.is_quarantined() and plugin.name not in self._quarantined:
                self._quarantined.append(plugin.name)
                logger.warning("[REGISTRY] Plugin %s quarantined", plugin.name)
            raise

    def _resolved_hooks(self, hook_name: str) -> Iterator[tuple]:
        """Yield (plugin, bound_method) for every active plugin that
        actually implements ``hook_name``."""
        for plugin in self._active_plugins():
            method = getattr(plugin, hook_name, None)
            if method is not None:
                yield plugin, method

    def call_hook(self, hook_name: str, *args: Any) -> List[Any]:
        """Call ``hook_name`` on every active plugin, collecting all results."""
        results = []
        for plugin, method in self._resolved_hooks(hook_name):
            try:
                results.append(self._call(plugin, method, args))
            except Exception:
                continue
        return results

    def call_hook_first(self, hook_name: str, *args: Any) -> Any:
        """Call ``hook_name`` until a plugin returns a non-None value."""
        for plugin, method in self._resolved_hooks(hook_name):
            try:
                result = self._call(plugin, method, args)
            except Exception:
                continue
            if result is not None:
                return result
        return None

    def call_hook_bool(self, hook_name: str, *args: Any) -> bool:
        """Call ``hook_name`` on every active plugin; False from any one
        of them makes the overall result False (e.g. validation vetoes)."""
        ok = True
        for plugin, method in self._resolved_hooks(hook_name):
            try:
                result = self._call(plugin, method, args)
            except Exception:
                continue
            if result is False:
                ok = False
        return ok

    def __repr__(self) -> str:
        return f"PluginRegistry(plugins={len(self._plugins)}, ready={self._is_ready})"
