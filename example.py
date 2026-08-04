"""
Usage example + micro-benchmark.

Run with: python example.py
"""
import time

from fsm_core import BasePlugin, GraphEngine, GraphUtils


def usage_example() -> None:
    engine = GraphEngine()
    root = engine.create_pipeline("root")

    fetch = engine.create_node("fetch")
    transform = engine.create_node("transform")
    load = engine.create_node("load")
    for node in (fetch, transform, load):
        engine.add_node(root, node)

    class LoggingPlugin(BasePlugin):
        name = "logging"
        version = "1.0"

        def on_node_added(self, pipeline, node, index, context):
            print(f"  + {node.id} added to {pipeline.id}")

    engine.register_plugin(LoggingPlugin())
    engine.ready()

    is_valid, cycle_nodes = engine.validate()
    print(f"valid={is_valid}, cycle_nodes={[n.id for n in cycle_nodes]}")

    engine.execute(root)
    print("tasks:", [n.id for n in root.get_tasks()])


def benchmark() -> None:
    print("\n=== BENCHMARK ===")
    engine = GraphEngine()

    for size in [100, 500, 1000, 5000, 20000]:
        start = time.perf_counter()
        pipeline = engine.create_pipeline()
        nodes = [engine.create_node() for _ in range(size)]
        for node in nodes:
            engine.add_node(pipeline, node)
        elapsed = time.perf_counter() - start
        print(f"Create {size:6} nodes: {elapsed:.4f}s ({size / elapsed:.0f} nodes/s)")

    root = engine.get_root()
    for mode in ["dfs", "bfs"]:
        start = time.perf_counter()
        result = GraphUtils.walk(root, mode=mode)
        elapsed = time.perf_counter() - start
        print(f"Walk {mode:4} {len(result):6} nodes: {elapsed:.4f}s")

    # A long chain, specifically to demonstrate the traversal no longer
    # depends on Python's recursion limit.
    chain_engine = GraphEngine()
    chain_root = chain_engine.create_pipeline()
    prev = chain_root
    for i in range(10_000):
        n = chain_engine.create_node()
        chain_engine.add_node(prev, n)
        prev = n

    start = time.perf_counter()
    is_valid, _ = chain_engine.validate()
    elapsed = time.perf_counter() - start
    print(f"Validate 10000-deep chain: {elapsed:.4f}s (valid={is_valid})")

    start = time.perf_counter()
    depth = GraphUtils.get_depth(chain_root)
    elapsed = time.perf_counter() - start
    print(f"Depth of 10000-deep chain: {elapsed:.4f}s (depth={depth})")

    print("=== BENCHMARK COMPLETE ===")


if __name__ == "__main__":
    usage_example()
    benchmark()
