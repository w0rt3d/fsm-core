"""
graph_utils.py has no pydantic dependency (BaseNode is only imported under
TYPE_CHECKING), so it's tested here against plain stand-in objects. This
keeps the fast/pure-algorithm tests runnable even in environments where
pydantic isn't installed.
"""
from fsm_core.graph_utils import GraphUtils


class Node:
    def __init__(self, id, children=None, parents=None):
        self.id = id
        self.children = children or []
        self.parents = parents or []


def chain(n):
    nodes = [Node(str(i)) for i in range(n)]
    for i in range(n - 1):
        nodes[i].children.append(nodes[i + 1])
        nodes[i + 1].parents.append(nodes[i])
    return nodes


def test_deep_chain_dfs_bfs_no_recursion_error():
    nodes = chain(6000)
    assert len(GraphUtils.get_flat_list(nodes[0])) == 6000
    assert len(GraphUtils.walk(nodes[0], mode="bfs")) == 6000
    assert GraphUtils.get_depth(nodes[0]) == 6000


def test_cycle_detection():
    a, b, c = Node("a"), Node("b"), Node("c")
    a.children = [b]
    b.parents = [a]
    b.children = [c]
    c.parents = [b]
    c.children = [a]
    a.parents = [c]  # a -> b -> c -> a

    is_valid, cycle_nodes = GraphUtils.validate(a)
    assert not is_valid
    assert cycle_nodes


def test_no_false_positive_cycle_on_diamond():
    # root -> {left, right} -> merge  (a DAG diamond, NOT a cycle)
    root, left, right, merge = Node("root"), Node("left"), Node("right"), Node("merge")
    root.children = [left, right]
    left.parents = [root]
    right.parents = [root]
    left.children = [merge]
    right.children = [merge]
    merge.parents = [left, right]

    is_valid, cycle_nodes = GraphUtils.validate(root)
    assert is_valid
    assert not cycle_nodes


def test_find_node_on_branching_tree():
    root, left, right = Node("root"), Node("left"), Node("right")
    root.children = [left, right]
    left.parents = [root]
    right.parents = [root]

    assert GraphUtils.find_node(root, "left") is left
    assert GraphUtils.find_node(root, "right") is right
    assert GraphUtils.find_node(root, "missing") is None


def test_is_ancestor_of():
    p, child = Node("p"), Node("ch")
    p.children = [child]
    child.parents = [p]

    assert GraphUtils.is_ancestor_of(p, child) is True
    assert GraphUtils.is_ancestor_of(child, p) is False
