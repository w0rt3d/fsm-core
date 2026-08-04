# fsm_core

A small in-memory DAG engine: typed nodes/pipelines (pydantic), an
iterative graph-algorithms layer, a priority-ordered event bus, and a
plugin system with per-plugin health tracking (quarantine on repeated
failures).

```
fsm_core/
├── __init__.py       # public API surface
├── _errors.py         # shared error-wrapping decorator (internal)
├── exceptions.py       # exception hierarchy
├── context.py          # Context / ContextManager
├── node.py             # BaseNode, BasePipeline
├── graph_utils.py      # iterative traversal, cycle detection, lookups
├── event.py             # EventBus, EventPriority
├── plugins.py           # BasePlugin, PluginRegistry, PluginHealth
├── dispatcher.py         # EventBus -> plugin hook bridge
└── engine.py             # GraphEngine: the object you actually use

tests/
├── test_graph_utils.py  # pure-Python, no pydantic needed
└── test_core.py          # full engine, needs pydantic

example.py                # usage example + perf benchmark
```

This reflects the actual module layout. (The previous README described a
`fsm_plugins/` package and `validator.py`/`logger.py` modules that don't
exist in the code — that's been corrected here.)

## Install

```bash
pip install -r requirements.txt
# for tests:
pip install -r requirements-dev.txt
```

## Quick start

```python
from fsm_core import GraphEngine

engine = GraphEngine()
root = engine.create_pipeline("root")

fetch = engine.create_node("fetch")
transform = engine.create_node("transform")
load = engine.create_node("load")

engine.add_node(root, fetch)
engine.add_node(root, transform)
engine.add_node(root, load)

is_valid, cycle_nodes = engine.validate()
assert is_valid

engine.execute(root)
```

## Plugins

```python
from fsm_core import BasePlugin

class LoggingPlugin(BasePlugin):
    name = "logging"
    version = "1.0"

    def on_node_added(self, pipeline, node, index, context):
        print(f"{node.id} added to {pipeline.id}")

engine.register_plugin(LoggingPlugin())
engine.ready()
```

A plugin that raises on 3 consecutive hook calls is automatically
quarantined (skipped) until `reset_health()` is called on it — see
`PluginHealth` in `plugins.py`.

## Tests

```bash
pytest tests/ -v
```

`test_graph_utils.py` only needs the standard library. `test_core.py`
exercises the full engine and needs pydantic installed.

## Design notes

- All graph operations are iterative — no Python recursion limit. Tested on 6000-node chains.
- Duplicate child detection is O(1) via internal `_child_ids`/`_parent_ids` sets.
- Plugin hook return values are advisory-only except `on_pipeline_validated` (returns `False` to veto execution). Other hooks influence the graph by mutating it directly or emitting new events.