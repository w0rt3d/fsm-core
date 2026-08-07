from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar, Dict, List, Literal, Optional, Set, Type

from pydantic import BaseModel, Field, PrivateAttr

from fsm_core._errors import wrap_errors
from fsm_core.exceptions import (
    CycleDetectedError,
    DuplicateNodeError,
    GraphError,
    InvalidOperationError,
)
from fsm_core.graph_utils import GraphUtils

logger = logging.getLogger(__name__)


class _Validate:
    @staticmethod
    def add_child(child: BaseNode, self_id: str, child_ids: Set[str]) -> None:
        if not isinstance(child, BaseNode):
            raise InvalidOperationError("Child must be a BaseNode instance")
        if self_id == child.id:
            raise InvalidOperationError("Cannot add node as its own child")
        if child.id in child_ids:
            raise DuplicateNodeError(f"Child node {child.id} already exists")

    @staticmethod
    def add_parent(parent: BaseNode, self_id: str, parent_ids: Set[str]) -> None:
        if not isinstance(parent, BaseNode):
            raise InvalidOperationError("Parent must be a BaseNode instance")
        if self_id == parent.id:
            raise InvalidOperationError("Cannot add node as its own parent")
        if parent.id in parent_ids:
            raise DuplicateNodeError(f"Parent node {parent.id} already exists")


class BaseNode(BaseModel):
    """
    A node in the graph. Supports multiple parents (it's a DAG, not just a
    tree) which is why ``parents`` is a list rather than a single optional
    reference.

    IDENTITY, NOT VALUE, EQUALITY
    ------------------------------
    Pydantic's default ``BaseModel.__eq__`` compares every field, including
    ``children`` and ``parents``. Because those two fields reference each
    other (a parent's ``children`` list contains its children, each of
    whose ``parents`` list points back at the parent), the default
    equality check recurses through that reference cycle -- in the
    original version of this class, any ``node_a == node_b`` or
    ``some_list.remove(node)`` call could recurse into the *entire*
    connected component of the graph, and does so again for every nested
    comparison, which is both extremely slow and, for graphs with actual
    reference cycles (a node reachable from itself via parent/child
    links), unbounded.

    Nodes are identified by ``id`` everywhere else in this codebase, so
    equality is redefined to match: two nodes are equal iff they have the
    same id. This also makes ``list.remove(node)``, ``x in some_list`` and
    using nodes as dict/set keys behave the way the rest of the code
    already assumed they did.
    """

    model_config = {"arbitrary_types_allowed": True}

    # Maps the discriminator value of the `type` field (e.g. "node",
    # "pipeline", or whatever a custom subclass declares) to the class
    # itself. Populated automatically -- see __init_subclass__ below --
    # so that (de)serialization can rebuild the right class from a plain
    # dict without the caller having to pass a type->class mapping by
    # hand. BaseNode itself is registered explicitly right after the
    # class body, since __init_subclass__ only fires for subclasses.
    _type_registry: ClassVar[Dict[str, Type["BaseNode"]]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        # NOT the plain `__init_subclass__`: pydantic calls that before
        # the subclass's own `model_fields` are finalized, so
        # `cls.model_fields["type"].default` would still read the
        # *parent's* default at that point (confirmed by hand -- it
        # silently registered every subclass under "node"/"pipeline").
        # `__pydantic_init_subclass__` is pydantic v2's hook for exactly
        # this: it fires once the subclass's own fields are resolved.
        super().__pydantic_init_subclass__(**kwargs)
        type_field = cls.model_fields.get("type")
        default = getattr(type_field, "default", None) if type_field else None
        if isinstance(default, str):
            existing = BaseNode._type_registry.get(default)
            if existing is not None and existing is not cls:
                logger.warning(
                    "[NODE] type %r re-registered: %s -> %s (last one wins)",
                    default, existing.__name__, cls.__name__,
                )
            BaseNode._type_registry[default] = cls

    @classmethod
    def register_type(cls, type_name: str, node_cls: Type["BaseNode"]) -> None:
        """Manual escape hatch for classes that don't declare `type` as a
        plain string default (e.g. build it dynamically)."""
        BaseNode._type_registry[type_name] = node_cls

    @classmethod
    def class_for_type(cls, type_name: str) -> Type["BaseNode"]:
        try:
            return BaseNode._type_registry[type_name]
        except KeyError as e:
            raise InvalidOperationError(f"Unknown node type: {type_name!r}") from e

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: Literal["node"] = "node"
    parents: List[BaseNode] = Field(default_factory=list, exclude=True)
    children: List[BaseNode] = Field(default_factory=list, exclude=True)
    extensions: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _child_ids: Set[str] = PrivateAttr(default_factory=set)
    _parent_ids: Set[str] = PrivateAttr(default_factory=set)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.id or not self.id.strip():
            raise InvalidOperationError("Node id cannot be empty")

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, BaseNode) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def add_child(self, child: BaseNode, index: Optional[int] = None) -> BaseNode:
        _Validate.add_child(child, self.id, self._child_ids)

        if GraphUtils.is_ancestor_of(child, self):
            raise CycleDetectedError(f"Adding child {child.id} would create a cycle")

        if index is not None:
            self.children.insert(index, child)
        else:
            self.children.append(child)
        self._child_ids.add(child.id)
        child.parents.append(self)
        child._parent_ids.add(self.id)

        logger.debug("[NODE] %s added to %s", child.id, self.id)
        return self

    @wrap_errors(InvalidOperationError)
    def remove_child(self, child_id: str) -> Optional[BaseNode]:
        for i, child in enumerate(self.children):
            if child.id == child_id:
                removed = self.children.pop(i)
                self._child_ids.discard(child_id)
                removed.parents = [p for p in removed.parents if p.id != self.id]
                removed._parent_ids.discard(self.id)
                logger.debug("[NODE] %s removed from %s", child_id, self.id)
                return removed
        return None

    @wrap_errors(InvalidOperationError, passthrough=(GraphError,))
    def add_parent(self, parent: BaseNode) -> BaseNode:
        _Validate.add_parent(parent, self.id, self._parent_ids)

        if GraphUtils.is_ancestor_of(self, parent):
            raise CycleDetectedError(f"Adding parent {parent.id} would create a cycle")

        self.parents.append(parent)
        self._parent_ids.add(parent.id)
        parent.children.append(self)
        parent._child_ids.add(self.id)
        logger.debug("[NODE] %s added as parent of %s", parent.id, self.id)
        return self

    @wrap_errors(InvalidOperationError)
    def remove_parent(self, parent_id: str) -> Optional[BaseNode]:
        for i, parent in enumerate(self.parents):
            if parent.id == parent_id:
                removed = self.parents.pop(i)
                self._parent_ids.discard(parent_id)
                removed.children = [c for c in removed.children if c.id != self.id]
                removed._child_ids.discard(self.id)
                logger.debug("[NODE] %s removed from %s", parent_id, self.id)
                return removed
        return None

    @wrap_errors(InvalidOperationError)
    def clone(self) -> BaseNode:
        return self.model_copy(deep=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id!r}, children={len(self.children)})"


class BasePipeline(BaseNode):
    type: Literal["pipeline"] = "pipeline"

    def add_task(self, task: BaseNode, index: Optional[int] = None) -> BasePipeline:
        self.add_child(task, index)
        return self

    def remove_task(self, task_id: str) -> Optional[BaseNode]:
        return self.remove_child(task_id)

    def find_task(self, task_id: str) -> Optional[BaseNode]:
        return GraphUtils.find_node(self, task_id)

    def get_tasks(self) -> List[BaseNode]:
        return self.children

    def __repr__(self) -> str:
        return f"BasePipeline(id={self.id!r}, tasks={len(self.children)})"


BaseNode.register_type("node", BaseNode)
BaseNode.register_type("pipeline", BasePipeline)