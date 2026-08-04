"""
Regression tests for the bugs found and fixed while rewriting fsm_core.

Requires pydantic (not needed by graph_utils tests, which are pure
Python). Run with: pytest tests/test_core.py -v
"""
import sys

import pytest

from fsm_core.engine import GraphEngine
from fsm_core.exceptions import CycleDetectedError
from fsm_core.graph_utils import GraphUtils
from fsm_core.plugins import BasePlugin


def test_deep_chain_traversal_does_not_hit_recursion_limit():
    """The original recursive DFS/cycle-detection blew the default 1000
    frame recursion limit on chains this repo's own benchmark builds."""
    e = GraphEngine()
    root = e.create_pipeline("root")
    prev = root
    nodes = []
    for i in range(6000):
        n = e.create_node(f"n{i}")
        e.add_node(prev, n)
        prev = n
        nodes.append(n)

    flat = e.get_flat_list()
    assert len(flat) == 6001  # root + 6000 nodes

    is_valid, cycles = e.validate()
    assert is_valid and not cycles


def test_add_child_rejects_cycle_in_both_directions():
    """BUG: `is_ancestor_of(self, child)` in add_child had the check
    inverted (it should be `is_ancestor_of(child, self)`), so building a
    two-node cycle via add_child was silently accepted and produced a
    real reference cycle in the graph."""
    e = GraphEngine()
    c1 = e.create_node("c1")
    c2 = e.create_node("c2")
    c1.add_child(c2)
    with pytest.raises(CycleDetectedError):
        c2.add_child(c1)


def test_node_equality_is_by_id_not_by_value():
    """BUG: pydantic's default __eq__ compares every field, including
    `children`/`parents`, which reference each other -- comparing two
    connected nodes could recurse through the whole component. Equality
    is redefined to be id-based, so it's O(1) and cycle-safe."""
    e = GraphEngine()
    a = e.create_node("a")
    b = e.create_node("b")
    a.add_child(b)

    sys.setrecursionlimit(200)  # deliberately low: proves == doesn't recurse
    assert a == a
    assert a != b
    assert a in [a]


def test_find_node_works_on_branching_trees():
    """BUG: find_node used to walk the *whole* tree and return
    result[-1], which only happened to be the target on unbranching
    chains. On any tree with more than one branch it silently returned
    the wrong node or None."""
    e = GraphEngine()
    root = e.create_pipeline("root")
    left = e.create_node("left")
    right = e.create_node("right")
    e.add_node(root, left)
    e.add_node(root, right)

    assert e.find_node("left") is not None and e.find_node("left").id == "left"
    assert e.find_node("right") is not None and e.find_node("right").id == "right"


def test_node_removed_and_node_moved_hooks_actually_fire():
    """BUG: the dispatcher read event.data["pipeline"] for node_removed
    (engine only ever emitted "node"), and event.data["old_parent"]
    (singular) for node_moved (engine emitted "old_parents", plural) --
    both hooks were dead code."""
    received = {}

    class Recorder(BasePlugin):
        name = "recorder"
        version = "1.0"

        def on_node_removed(self, node, parents, context):
            received["removed"] = (node.id, [p.id for p in parents])

        def on_node_moved(self, node, old_parents, new_parent, context):
            received["moved"] = (node.id, [p.id for p in old_parents], new_parent.id)

    e = GraphEngine()
    e.register_plugin(Recorder())
    e.ready()
    root = e.create_pipeline("root")
    target = e.create_pipeline("target")
    x = e.create_node("x")
    e.add_node(root, x)
    e.add_node(root, target)

    e.move_node("x", "target")
    assert received["moved"] == ("x", ["root"], "target")

    e.remove_node("x")
    assert received["removed"] == ("x", ["target"])


def test_plugin_quarantine_after_repeated_failures():
    class Flaky(BasePlugin):
        name = "flaky"
        version = "1.0"

        def on_node_created(self, node, context):
            raise RuntimeError("boom")

    e = GraphEngine()
    e.register_plugin(Flaky())
    e.ready()
    for i in range(5):
        e.create_node(f"n{i}")

    plugin = e.get_plugin("flaky")
    assert plugin.is_quarantined()
    assert plugin.error_count() == 3  # default max_errors


def test_context_defaults_without_metadata():
    """BUG: Context.metadata had no default, so Context() raised a
    pydantic ValidationError; every caller had to pass metadata={}."""
    from fsm_core.context import Context

    ctx = Context()
    assert ctx.metadata == {}

def test_collapse_deep_chain_does_not_hit_recursion_limit():
    e = GraphEngine()
    root = e.create_pipeline("root")
    nodes = [e.create_node(f"n{i}") for i in range(2000)]
    e.add_node(root, nodes[0])
    for i in range(len(nodes) - 1):
        nodes[i].add_child(nodes[i + 1])
    
    e.collapse(root)
    assert len(root.get_tasks()) == 0
    for node in nodes:
        assert node.id not in e._nodes

import pytest
import sys

from fsm_core.engine import GraphEngine
from fsm_core.exceptions import CycleDetectedError, InvalidOperationError
from fsm_core.graph_utils import GraphUtils
from fsm_core.plugins import BasePlugin
from fsm_core.node import BaseNode, BasePipeline


def test_deep_chain_traversal_does_not_hit_recursion_limit():
    e = GraphEngine()
    root = e.create_pipeline("root")
    prev = root
    nodes = []
    for i in range(6000):
        n = e.create_node(f"n{i}")
        e.add_node(prev, n)
        prev = n
        nodes.append(n)

    flat = e.get_flat_list()
    assert len(flat) == 6001

    is_valid, cycles = e.validate()
    assert is_valid and not cycles


def test_add_child_rejects_cycle_in_both_directions():
    e = GraphEngine()
    c1 = e.create_node("c1")
    c2 = e.create_node("c2")
    c1.add_child(c2)
    with pytest.raises(CycleDetectedError):
        c2.add_child(c1)


def test_node_equality_is_by_id_not_by_value():
    e = GraphEngine()
    a = e.create_node("a")
    b = e.create_node("b")
    a.add_child(b)

    sys.setrecursionlimit(200)
    assert a == a
    assert a != b
    assert a in [a]


def test_find_node_works_on_branching_trees():
    e = GraphEngine()
    root = e.create_pipeline("root")
    left = e.create_node("left")
    right = e.create_node("right")
    e.add_node(root, left)
    e.add_node(root, right)

    assert e.find_node("left") is not None and e.find_node("left").id == "left"
    assert e.find_node("right") is not None and e.find_node("right").id == "right"


def test_node_removed_and_node_moved_hooks_actually_fire():
    received = {}

    class Recorder(BasePlugin):
        name = "recorder"
        version = "1.0"

        def on_node_removed(self, node, parents, context):
            received["removed"] = (node.id, [p.id for p in parents])

        def on_node_moved(self, node, old_parents, new_parent, context):
            received["moved"] = (node.id, [p.id for p in old_parents], new_parent.id)

    e = GraphEngine()
    e.register_plugin(Recorder())
    e.ready()
    root = e.create_pipeline("root")
    target = e.create_pipeline("target")
    x = e.create_node("x")
    e.add_node(root, x)
    e.add_node(root, target)

    e.move_node("x", "target")
    assert received["moved"] == ("x", ["root"], "target")

    e.remove_node("x")
    assert received["removed"] == ("x", ["target"])


def test_plugin_quarantine_after_repeated_failures():
    class Flaky(BasePlugin):
        name = "flaky"
        version = "1.0"

        def on_node_created(self, node, context):
            raise RuntimeError("boom")

    e = GraphEngine()
    e.register_plugin(Flaky())
    e.ready()
    for i in range(5):
        e.create_node(f"n{i}")

    plugin = e.get_plugin("flaky")
    assert plugin.is_quarantined()
    assert plugin.error_count() == 3


def test_context_defaults_without_metadata():
    from fsm_core.context import Context

    ctx = Context()
    assert ctx.metadata == {}


def test_collapse_deep_chain_does_not_hit_recursion_limit():
    e = GraphEngine()
    root = e.create_pipeline("root")
    nodes = [e.create_node(f"n{i}") for i in range(2000)]
    e.add_node(root, nodes[0])
    for i in range(len(nodes) - 1):
        nodes[i].add_child(nodes[i + 1])

    e.collapse(root)
    assert len(root.get_tasks()) == 0
    for node in nodes:
        assert node.id not in e._nodes


def test_clone_preserves_id_but_not_identity():
    a = BaseNode(id="original")
    a.metadata["key"] = "value"
    b = a.clone()
    assert b.id == a.id
    assert b.metadata == a.metadata
    assert b is not a


def test_add_child_with_index():
    a = BaseNode(id="a")
    b = BaseNode(id="b")
    c = BaseNode(id="c")
    a.add_child(b)
    a.add_child(c, index=0)
    assert [child.id for child in a.children] == ["c", "b"]


def test_expand_preserves_connections():
    e = GraphEngine()
    parent = e.create_node("parent")
    child_old = e.create_node("child")
    parent.add_child(child_old)

    new_children = [e.create_node(f"new_{i}") for i in range(3)]
    pipeline = e.expand(child_old, new_children)

    assert pipeline.id == child_old.id
    assert pipeline in parent.children
    assert len(pipeline.get_tasks()) == 3
    assert child_old.id not in e._nodes


def test_simplify_flattens_single_child_pipeline():
    e = GraphEngine()
    root = e.create_pipeline("root")
    inner = e.create_pipeline("inner")
    leaf = e.create_node("leaf")
    e.add_node(root, inner)
    e.add_node(inner, leaf)

    e.simplify(root)
    assert leaf in root.get_tasks()
    assert inner.id not in e._nodes


def test_replace_node_updates_root():
    e = GraphEngine()
    old_root = e.create_pipeline("old_root")
    new_root = BasePipeline(id="new_root")

    e.replace_node(old_root, new_root)
    assert e.get_root().id == "new_root"
    assert old_root.id not in e._nodes


def test_move_node_multiple_parents():
    e = GraphEngine()
    root = e.create_pipeline("root")
    p1 = e.create_pipeline("p1")
    p2 = e.create_pipeline("p2")
    x = e.create_node("x")
    e.add_node(root, p1)
    e.add_node(root, p2)
    e.add_node(p1, x)
    p2.add_child(x)

    assert len(x.parents) == 2

    target = e.create_pipeline("target")
    e.add_node(root, target)
    e.move_node("x", "target")

    assert len(x.parents) == 1
    assert x.parents[0].id == "target"
    assert x not in p1.get_tasks()
    assert x not in p2.get_tasks()


def test_full_pipeline_execute_with_plugins():
    executed = []

    class ExecPlugin(BasePlugin):
        name = "exec"
        version = "1.0"

        def on_node_created(self, node, context):
            executed.append(("created", node.id))

        def on_pipeline_executed(self, pipeline, context):
            executed.append(("executed", pipeline.id))

    e = GraphEngine()
    e.register_plugin(ExecPlugin())
    e.ready()

    root = e.create_pipeline("root")
    node = e.create_node("task")
    e.add_node(root, node)
    e.execute(root)

    assert ("created", "task") in executed
    assert ("executed", "root") in executed