from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fsm_core._errors import wrap_errors
from fsm_core.context import Context, ContextManager
from fsm_core.dispatcher import EventDispatcher
from fsm_core.event import EventBus
from fsm_core.exceptions import GraphError, InvalidOperationError
from fsm_core.graph_utils import GraphUtils
from fsm_core.node import BaseNode, BasePipeline
from fsm_core.plugins import PluginRegistry

logger = logging.getLogger(__name__)


class GraphEngine:
    """
    Owns the node registry, the root pipeline, the event bus, the plugin
    registry and the current context -- the single object user code talks
    to.

    Root semantics: the *first* pipeline created via ``create_pipeline``
    becomes ``self._root``. Later calls to ``create_pipeline`` return
    independent, unattached pipelines (useful for building a subtree
    before splicing it in with ``add_node``); they do not replace the
    root. This was implicit in the original code -- documented here so
    it isn't rediscovered by trial and error.
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._nodes: Dict[str, BaseNode] = {}
        self._root: Optional[BasePipeline] = None
        self._registry = PluginRegistry()
        self._event_bus = EventBus(max_history=max_history)
        self._dispatcher = EventDispatcher(self._event_bus, self._registry)
        self._context_manager = ContextManager()

    # -- plugins / context / event bus accessors ------------------------

    def register_plugin(self, plugin) -> "GraphEngine":
        self._registry.register(plugin)
        return self

    def get_extension(self, name: str) -> Optional[callable]:
        return self._registry.get_extension(name)

    def get_plugin(self, name: str):
        return self._registry.get_plugin(name)

    def get_event_bus(self) -> EventBus:
        return self._event_bus

    def get_context(self) -> Optional[Context]:
        return self._context_manager.get_current()

    def ready(self) -> "GraphEngine":
        self._registry.ready()
        logger.info("[ENGINE] Ready")
        return self

    # -- node / pipeline creation -----------------------------------

    @wrap_errors(InvalidOperationError)
    def create_node(self, node_id: Optional[str] = None) -> BaseNode:
        node = BaseNode(id=node_id) if node_id else BaseNode()
        self._nodes[node.id] = node
        logger.debug("[ENGINE] Created node %s", node.id)
        self._event_bus.emit("node_created", data={"node": node})
        return node

    @wrap_errors(InvalidOperationError)
    def create_pipeline(self, pipeline_id: Optional[str] = None) -> BasePipeline:
        pipeline = BasePipeline(id=pipeline_id) if pipeline_id else BasePipeline()
        self._nodes[pipeline.id] = pipeline
        if self._root is None:
            self._root = pipeline
        logger.debug("[ENGINE] Created pipeline %s", pipeline.id)
        self._event_bus.emit("pipeline_created", data={"pipeline": pipeline})
        return pipeline

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def add_node(self, pipeline: BasePipeline, node: BaseNode, index: Optional[int] = None) -> "GraphEngine":
        pipeline.add_child(node, index)  # raises GraphError subclasses on its own
        self._nodes[node.id] = node
        logger.debug("[ENGINE] Added node %s to pipeline %s", node.id, pipeline.id)
        self._event_bus.emit("node_added", data={"pipeline": pipeline, "node": node, "index": index})
        return self

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def remove_node(self, node_id: str) -> Optional[BaseNode]:
        node = self._nodes.pop(node_id, None)
        if node is None:
            return None

        parents = node.parents[:]
        for parent in parents:
            parent.remove_child(node_id)
        for child in node.children[:]:
            node.remove_child(child.id)

        logger.debug("[ENGINE] Removed node %s", node_id)
        # `parents` (plural) matches BaseNode.parents: a node in a DAG can
        # have more than one parent, so a hook that only received "the"
        # pipeline it was removed from would be lossy by construction.
        self._event_bus.emit("node_removed", data={"node": node, "parents": parents})
        return node

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def move_node(self, node_id: str, new_parent_id: str, new_index: Optional[int] = None) -> "GraphEngine":
        if self._root is None:
            raise InvalidOperationError("Graph has no root; nothing to move")

        node = GraphUtils.find_node(self._root, node_id)
        if node is None:
            raise InvalidOperationError(f"Node {node_id} not found")
        new_parent = GraphUtils.find_node(self._root, new_parent_id)
        if new_parent is None:
            raise InvalidOperationError(f"Parent {new_parent_id} not found")

        old_parents = node.parents[:]
        for parent in old_parents:
            parent.remove_child(node_id)
        new_parent.add_child(node, new_index)

        logger.debug("[ENGINE] Moved node %s to %s", node_id, new_parent_id)
        self._event_bus.emit(
            "node_moved",
            data={"node": node, "old_parents": old_parents, "new_parent": new_parent},
        )
        return self

    # -- lookups -----------------------------------------------------

    def get_node(self, node_id: str) -> Optional[BaseNode]:
        return self._nodes.get(node_id)

    def get_root(self) -> Optional[BasePipeline]:
        return self._root

    def get_flat_list(self) -> List[BaseNode]:
        return GraphUtils.get_flat_list(self._root) if self._root else []

    def get_fqn(self, node_id: str) -> Optional[str]:
        node = self._nodes.get(node_id)
        return GraphUtils.get_fqn(node) if node else None

    def find_node(self, node_id: str) -> Optional[BaseNode]:
        return GraphUtils.find_node(self._root, node_id) if self._root else None

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def validate(self, pipeline: Optional[BasePipeline] = None) -> Tuple[bool, List[BaseNode]]:
        target = pipeline or self._root
        if target is None:
            return True, []
        is_valid, cycle_nodes = GraphUtils.validate(target)
        self._event_bus.emit(
            "pipeline_validated",
            data={"pipeline": target, "is_valid": is_valid, "cycle_nodes": cycle_nodes},
        )
        if cycle_nodes:
            self._event_bus.emit("cycle_detected", data={"pipeline": target, "cycle_nodes": cycle_nodes})
        return is_valid, cycle_nodes

    # -- execution / mutation notifications ---------------------------

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def execute(self, pipeline: BasePipeline, context: Optional[Context] = None) -> "GraphEngine":
        ctx = context or self._context_manager.get_current()
        is_valid, _ = self.validate(pipeline)
        if not is_valid:
            raise InvalidOperationError("Pipeline has cycles, cannot execute")

        self._event_bus.emit("pipeline_executed", data={"pipeline": pipeline}, context=ctx)
        logger.info("[ENGINE] Executed pipeline %s", pipeline.id)
        return self

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def mutate(self, pipeline: BasePipeline, context: Optional[Context] = None) -> "GraphEngine":
        ctx = context or self._context_manager.get_current()
        self._event_bus.emit("pipeline_mutated", data={"pipeline": pipeline}, context=ctx)
        self._event_bus.emit("graph_changed", data={"pipeline": pipeline}, context=ctx)
        logger.info("[ENGINE] Mutated pipeline %s", pipeline.id)
        return self

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def notify_changed(
        self, node: BaseNode, old_state: Dict[str, Any], new_state: Dict[str, Any], context: Optional[Context] = None
    ) -> "GraphEngine":
        ctx = context or self._context_manager.get_current()
        self._event_bus.emit(
            "node_changed", data={"node": node, "old_state": old_state, "new_state": new_state}, context=ctx
        )
        logger.debug("[ENGINE] Notified change for node %s", node.id)
        return self

    # -- structural rewrites --------------------------------------------
    #
    # These four (simplify/expand/collapse/replace_node) are the single
    # source of truth for structural graph rewrites. The original repo
    # additionally had near-duplicate copies of some of this logic living
    # directly on BaseNode in scratch files (hsj.py, "MIGHT BE NEED") --
    # two implementations of the same operation drifting apart is exactly
    # the kind of thing that becomes a debugging session six months from
    # now, so those were dropped and this is now the only version.

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def simplify(self, pipeline: BasePipeline) -> BasePipeline:
        """Collapse empty pipelines and flatten single-child pipelines,
        recursively, bottom-up."""

        def _simplify(node: BaseNode) -> BaseNode:
            for i, child in enumerate(node.children):
                node.children[i] = _simplify(child)

            i = 0
            while i < len(node.children):
                child = node.children[i]
                if not isinstance(child, BasePipeline):
                    i += 1
                    continue
                if not child.children:
                    node.children.pop(i)
                    child.parents.remove(node)
                    self._nodes.pop(child.id, None)
                    logger.info("[ENGINE] Removed empty pipeline %s", child.id)
                elif len(child.children) == 1:
                    grandchild = child.children[0]
                    node.children[i] = grandchild
                    grandchild.parents.remove(child)
                    grandchild.parents.append(node)
                    child.parents.clear()
                    child.children.clear()
                    self._nodes.pop(child.id, None)
                    logger.info("[ENGINE] Flattened single-child pipeline %s -> %s", child.id, grandchild.id)
                    i += 1
                else:
                    i += 1
            return node

        result = _simplify(pipeline)
        self._event_bus.emit("graph_changed", data={"pipeline": pipeline})
        return result

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def expand(self, node: BaseNode, children: List[BaseNode]) -> BasePipeline:
        """Replace a leaf node with a pipeline wrapping ``children``,
        re-wiring the leaf's former parents/children onto the new pipeline."""
        pipeline = BasePipeline(id=node.id)
        for child in children:
            pipeline.add_child(child)
            self._nodes[child.id] = child
        self._nodes[pipeline.id] = pipeline

        for parent in node.parents[:]:
            parent.children = [pipeline if c.id == node.id else c for c in parent.children]
            pipeline.parents.append(parent)
        for child in node.children[:]:
            child.parents = [pipeline if p.id == node.id else p for p in child.parents]
            pipeline.children.append(child)
        node.parents.clear()
        node.children.clear()
        self._nodes.pop(node.id, None)

        if self._root is not None and self._root.id == node.id:
            self._root = pipeline

        logger.info("[ENGINE] Expanded %s into pipeline with %d nodes", node.id, len(children))
        self._event_bus.emit("graph_changed", data={"pipeline": pipeline})
        return pipeline

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def collapse(self, node: BaseNode) -> BaseNode:
        """Fold a node's entire subtree into itself, iteratively (post-order)."""
        stack = [(node, False)]
        while stack:
            current, visited = stack.pop()
            if not visited:
                stack.append((current, True))
                for child in current.children[:]:
                    stack.append((child, False))
            else:
                for child in current.children[:]:
                    for parent in child.parents[:]:
                        if parent.id != current.id:
                            parent.children = [current if c.id == child.id else c for c in parent.children]
                            current.parents.append(parent)
                    child.parents.clear()
                    self._nodes.pop(child.id, None)
                current.children.clear()

        logger.info("[ENGINE] Collapsed %s", node.id)
        self._event_bus.emit("graph_changed", data={"pipeline": node})
        return node

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def replace_node(self, old_node: BaseNode, new_node: BaseNode) -> BaseNode:
        if old_node.id == new_node.id:
            raise InvalidOperationError("Cannot replace node with itself")

        for parent in old_node.parents[:]:
            parent.children = [new_node if c.id == old_node.id else c for c in parent.children]
            new_node.parents.append(parent)
        for child in old_node.children[:]:
            child.parents = [new_node if p.id == old_node.id else p for p in child.parents]
            new_node.children.append(child)

        old_node.parents.clear()
        old_node.children.clear()
        self._nodes.pop(old_node.id, None)
        self._nodes[new_node.id] = new_node

        if self._root is not None and self._root.id == old_node.id:
            self._root = new_node if isinstance(new_node, BasePipeline) else None

        logger.info("[ENGINE] Replaced %s with %s", old_node.id, new_node.id)
        self._event_bus.emit("graph_changed", data={"pipeline": self._root})
        return new_node

    def __repr__(self) -> str:
        return f"GraphEngine(nodes={len(self._nodes)}, root={self._root.id if self._root else None})"
