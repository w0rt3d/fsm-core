from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, List, Optional, Set, Tuple

from fsm_core._errors import wrap_errors
from fsm_core.exceptions import GraphError, InvalidOperationError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fsm_core.node import BaseNode


class _Validate:
    @staticmethod
    def walk(mode: str, direction: str, stop_when: Optional[Callable]) -> None:
        if not isinstance(mode, str):
            raise InvalidOperationError("Mode must be a string")
        if not isinstance(direction, str):
            raise InvalidOperationError("Direction must be a string")
        if mode not in ("dfs", "bfs"):
            raise InvalidOperationError(f"Unknown mode: {mode}. Use 'dfs' or 'bfs'")
        if direction not in ("children", "parents"):
            raise InvalidOperationError(f"Unknown direction: {direction}. Use 'children' or 'parents'")
        if stop_when is not None and not callable(stop_when):
            raise InvalidOperationError("stop_when must be callable or None")

    @staticmethod
    def find_node(node_id: str) -> None:
        if not isinstance(node_id, str):
            raise InvalidOperationError("node_id must be a string")
        if not node_id.strip():
            raise InvalidOperationError("node_id cannot be empty")


class GraphUtils:
    """
    Pure graph algorithms over BaseNode, with no state of their own.

    Every traversal here is iterative (explicit stack/queue), not
    recursive. The original implementation used Python-level recursion for
    DFS and cycle detection, one stack frame per graph node -- fine for a
    handful of nodes, but this package's own benchmark script builds
    chains of 1000+ nodes, which sits right at (and can exceed) Python's
    default recursion limit of 1000. An iterative walk has no such limit
    and is also measurably faster since it avoids Python function-call
    overhead per node.
    """

    # -- traversal -------------------------------------------------------

    @staticmethod
    @wrap_errors(GraphError, passthrough=(GraphError,))
    def walk(
        root: "BaseNode",
        mode: str = "dfs",
        direction: str = "children",
        stop_when: Optional[Callable[["BaseNode"], bool]] = None,
    ) -> List["BaseNode"]:
        _Validate.walk(mode, direction, stop_when)
        return (
            GraphUtils._walk_dfs(root, direction, stop_when)
            if mode == "dfs"
            else GraphUtils._walk_bfs(root, direction, stop_when)
        )

    @staticmethod
    def _walk_dfs(
        root: "BaseNode", direction: str, stop_when: Optional[Callable[["BaseNode"], bool]]
    ) -> List["BaseNode"]:
        visited: Set[str] = set()
        result: List["BaseNode"] = []
        # Explicit stack instead of recursion. Pushing reversed(neighbors)
        # keeps left-to-right visiting order identical to the recursive
        # version (first child visited first).
        stack: List["BaseNode"] = [root]

        while stack:
            node = stack.pop()
            if node.id in visited:
                continue
            visited.add(node.id)
            result.append(node)
            if stop_when and stop_when(node):
                continue
            neighbors = getattr(node, direction, [])
            stack.extend(reversed(neighbors))

        return result

    @staticmethod
    def _walk_bfs(
        root: "BaseNode", direction: str, stop_when: Optional[Callable[["BaseNode"], bool]]
    ) -> List["BaseNode"]:
        from collections import deque

        result: List["BaseNode"] = []
        visited: Set[str] = {root.id}
        queue: deque = deque([root])

        while queue:
            node = queue.popleft()
            result.append(node)
            if stop_when and stop_when(node):
                continue
            for neighbor in getattr(node, direction, []):
                if neighbor.id not in visited:
                    visited.add(neighbor.id)
                    queue.append(neighbor)

        return result

    # -- lookups -----------------------------------------------------

    @staticmethod
    def find_node(root: "BaseNode", node_id: str) -> Optional["BaseNode"]:
        """
        BUG FIX: the original implementation delegated to ``walk(...,
        stop_when=lambda n: n.id == node_id)`` and returned
        ``result[-1]``. ``stop_when`` only prunes a matched node's
        *children* -- it does not stop sibling branches from being
        visited afterwards -- so ``result[-1]`` was simply "whatever node
        the full traversal happened to visit last", which is only the
        target node by coincidence (e.g. in a single unbranching chain,
        which is exactly the shape this repo's own benchmark used, so the
        bug never showed up there). For any tree with more than one
        branch, this silently returned the wrong node -- or None, even
        when the target was clearly present. Confirmed with a test
        against the original logic before rewriting it.

        This version does a plain early-terminating DFS instead.
        """
        try:
            _Validate.find_node(node_id)
        except GraphError as e:
            logger.error("[UTILS] find_node failed: %s", e)
            return None

        visited: Set[str] = set()
        stack: List["BaseNode"] = [root]
        while stack:
            node = stack.pop()
            if node.id == node_id:
                return node
            if node.id in visited:
                continue
            visited.add(node.id)
            stack.extend(reversed(node.children))
        return None

    @staticmethod
    def get_root(node: "BaseNode") -> "BaseNode":
        current = node
        while current.parents:
            current = current.parents[0]
        return current

    @staticmethod
    @wrap_errors(GraphError, passthrough=(GraphError,))
    def get_flat_list(root: "BaseNode") -> List["BaseNode"]:
        return GraphUtils.walk(root, "dfs", "children")

    @staticmethod
    @wrap_errors(GraphError, passthrough=(GraphError,))
    def get_leaves(root: "BaseNode") -> List["BaseNode"]:
        return [n for n in GraphUtils.get_flat_list(root) if not n.children]

    @staticmethod
    @wrap_errors(GraphError)
    def get_fqn(node: "BaseNode") -> str:
        parts = [node.id]
        current = node
        while current.parents:
            current = current.parents[0]
            parts.append(current.id)
        return "/".join(reversed(parts))

    @staticmethod
    @wrap_errors(GraphError, passthrough=(GraphError,))
    def is_ancestor_of(ancestor: "BaseNode", descendant: "BaseNode") -> bool:
        result = GraphUtils.walk(
            root=descendant, mode="dfs", direction="parents", stop_when=lambda n: n.id == ancestor.id
        )
        return bool(result) and result[-1].id == ancestor.id

    @staticmethod
    def get_depth(root: "BaseNode") -> int:
        try:
            depth = 1
            current = root
            while current.children:
                depth += 1
                current = current.children[0]
            return depth
        except Exception as e:
            logger.exception("[UTILS] Unexpected error during get_depth")
            raise GraphError(f"get_depth failed: {e}") from e

    @staticmethod
    @wrap_errors(GraphError, passthrough=(GraphError,))
    def get_size(root: "BaseNode") -> int:
        return len(GraphUtils.get_flat_list(root))

    # -- cycle detection -----------------------------------------------

    @staticmethod
    def _detect_cycles_iterative(root: "BaseNode") -> List["BaseNode"]:
        """
        Iterative three-colour (white/gray/black) DFS cycle detection.

        A node is "gray" while it is on the current DFS path (an ancestor
        of the node currently being expanded) and "black" once its whole
        subtree has been fully explored. Finding an edge into a gray node
        means we've looped back onto the current path, i.e. a cycle.

        Uses an explicit stack of ``[node, next_child_index]`` frames
        instead of the Python call stack, so unlike the original recursive
        version it has no depth limit. The returned list preserves the
        original's behaviour of reporting the *whole* cycle path (the
        closing node followed by every ancestor back up to the root),
        not just the single back-edge.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict = {root.id: GRAY}
        # frame = [node, index of the next child to visit]
        stack: List[list] = [[root, 0]]

        while stack:
            node, idx = stack[-1]
            if idx < len(node.children):
                stack[-1][1] += 1
                child = node.children[idx]
                state = color.get(child.id, WHITE)
                if state == GRAY:
                    cycle_nodes = [child] + [frame[0] for frame in reversed(stack)]
                    logger.warning("[UTILS] Cycle detected at %s", child.id)
                    return cycle_nodes
                if state == WHITE:
                    color[child.id] = GRAY
                    stack.append([child, 0])
                # BLACK children are already fully explored: nothing to do
            else:
                color[node.id] = BLACK
                stack.pop()

        return []

    @staticmethod
    @wrap_errors(GraphError)
    def has_cycles(root: "BaseNode") -> bool:
        return bool(GraphUtils._detect_cycles_iterative(root))

    @staticmethod
    @wrap_errors(GraphError)
    def detect_cycles(root: "BaseNode") -> List["BaseNode"]:
        return GraphUtils._detect_cycles_iterative(root)

    @staticmethod
    @wrap_errors(GraphError, passthrough=(GraphError,))
    def validate(root: "BaseNode") -> Tuple[bool, List["BaseNode"]]:
        cycle_nodes = GraphUtils.detect_cycles(root)
        if cycle_nodes:
            logger.warning("[UTILS] Cycle detected in graph with root %s", root.id)
            return False, cycle_nodes
        return True, []
