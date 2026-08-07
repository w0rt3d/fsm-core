"""
Tests for GraphEngine (de)serialization: to_dict/from_dict, to_json/from_json,
to_bson/from_bson, and the BaseNode type registry that makes custom node
subclasses round-trip correctly.

Run with: pytest tests/test_serialization.py -v
"""
from typing import Literal

import pytest

from fsm_core.engine import GraphEngine
from fsm_core.exceptions import SerializationError
from fsm_core.node import BaseNode, BasePipeline


class TaskNode(BaseNode):
    """A custom node subclass with its own field, used to check that
    (de)serialization preserves subclass-specific data and picks the
    right class back up via the type registry."""

    type: Literal["task"] = "task"
    status: str = "pending"


def _build_sample_engine() -> GraphEngine:
    e = GraphEngine()
    root = e.create_pipeline("root")
    a = TaskNode(id="a", status="done")
    e.add_existing_node(a)
    e.add_node(root, a)
    b = e.create_node("b")
    e.add_node(root, b)
    # diamond: c has two parents (a and b)
    c = TaskNode(id="c", status="pending")
    e.add_existing_node(c)
    e.add_node(a, c)
    e.add_node(b, c)
    return e


def test_to_dict_round_trip_preserves_structure_and_root():
    e = _build_sample_engine()
    data = e.to_dict()

    e2 = GraphEngine.from_dict(data)

    assert e2.get_root().id == "root"
    ids = {n.id for n in e2.get_all_nodes()}
    assert ids == {"root", "a", "b", "c"}

    root2 = e2.get_root()
    child_ids = {c.id for c in root2.children}
    assert child_ids == {"a", "b"}

    c2 = e2.get_node("c")
    assert {p.id for p in c2.parents} == {"a", "b"}


def test_custom_subclass_fields_and_type_survive_round_trip():
    e = _build_sample_engine()
    data = e.to_dict()
    e2 = GraphEngine.from_dict(data)

    a2 = e2.get_node("a")
    assert isinstance(a2, TaskNode)
    assert a2.status == "done"

    c2 = e2.get_node("c")
    assert isinstance(c2, TaskNode)
    assert c2.status == "pending"

    # plain node stayed a plain node/pipeline, not a TaskNode
    b2 = e2.get_node("b")
    assert type(b2) is BaseNode
    root2 = e2.get_root()
    assert type(root2) is BasePipeline


def test_get_all_nodes_includes_orphans_not_reachable_from_root():
    e = GraphEngine()
    e.create_pipeline("root")
    e.create_node("orphan")  # never attached anywhere

    all_ids = {n.id for n in e.get_all_nodes()}
    assert "orphan" in all_ids
    # get_flat_list, by contrast, only sees what's reachable from root
    assert "orphan" not in {n.id for n in e.get_flat_list()}

    # and the orphan survives a round trip too
    e2 = GraphEngine.from_dict(e.to_dict())
    assert "orphan" in {n.id for n in e2.get_all_nodes()}


def test_json_round_trip():
    e = _build_sample_engine()
    raw = e.to_json()
    assert isinstance(raw, str)

    e2 = GraphEngine.from_json(raw)
    assert e2.get_root().id == "root"
    assert {n.id for n in e2.get_all_nodes()} == {"root", "a", "b", "c"}


def test_from_json_rejects_invalid_json():
    with pytest.raises(SerializationError):
        GraphEngine.from_json("{not valid json")


def test_from_dict_rejects_dangling_child_reference():
    data = {
        "root_id": "root",
        "nodes": [
            {"id": "root", "type": "pipeline", "extensions": {}, "metadata": {}, "children": ["ghost"]},
        ],
    }
    with pytest.raises(SerializationError):
        GraphEngine.from_dict(data)


def test_from_dict_rejects_unknown_node_type():
    data = {
        "root_id": "root",
        "nodes": [
            {"id": "root", "type": "does_not_exist", "extensions": {}, "metadata": {}, "children": []},
        ],
    }
    with pytest.raises(SerializationError):
        GraphEngine.from_dict(data)


def test_to_bson_without_bson_installed_raises_clear_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "bson":
            raise ImportError("simulated: bson not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    e = _build_sample_engine()
    with pytest.raises(SerializationError, match="bson"):
        e.to_bson()


def test_bson_round_trip_if_available():
    bson = pytest.importorskip("bson")
    e = _build_sample_engine()
    raw = e.to_bson()
    assert isinstance(raw, bytes)

    e2 = GraphEngine.from_bson(raw)
    assert e2.get_root().id == "root"
    assert {n.id for n in e2.get_all_nodes()} == {"root", "a", "b", "c"}
