# Class Diagram (UML, Mermaid)

## Граф данных: `BaseNode` / `BasePipeline`

```mermaid
classDiagram
    class BaseNode {
        +str id
        +Literal type = "node"
        -List~BaseNode~ parents
        -List~BaseNode~ children
        +Dict extensions
        +Dict metadata
        -Set~str~ _child_ids
        -Set~str~ _parent_ids
        +add_child(child, index) BaseNode
        +remove_child(child_id) BaseNode?
        +add_parent(parent) BaseNode
        +remove_parent(parent_id) BaseNode?
        +clone() BaseNode
        +__eq__(other) bool
        +__hash__() int
    }

    class BasePipeline {
        +Literal type = "pipeline"
        +add_task(task, index) BasePipeline
        +remove_task(task_id) BaseNode?
        +find_task(task_id) BaseNode?
        +get_tasks() List~BaseNode~
    }

    BaseNode <|-- BasePipeline : наследование
    BaseNode "1" o-- "0..*" BaseNode : parents / children\n(двунаправленно, DAG)
```

`parents`/`children` помечены как `-` (private по UML-нотации) не потому,
что реально скрыты Python-инкапсуляцией (это обычные публичные атрибуты
pydantic-модели), а потому что оба поля объявлены с `exclude=True` —
исключены из сериализации (`model_dump()`/JSON). Ассоциация `BaseNode
"1" o-- "0..*" BaseNode` двунаправленная и симметрично поддерживается
методами `add_child`/`add_parent` — единственный способ гарантированно не
рассинхронизировать обе стороны связи.

## Фасад и его зависимости

```mermaid
classDiagram
    class GraphEngine {
        -Dict~str,BaseNode~ _nodes
        -BasePipeline? _root
        -PluginRegistry _registry
        -EventBus _event_bus
        -EventDispatcher _dispatcher
        -ContextManager _context_manager
        +create_node(node_id) BaseNode
        +create_pipeline(pipeline_id) BasePipeline
        +add_node(pipeline, node, index) GraphEngine
        +remove_node(node_id) BaseNode?
        +move_node(node_id, new_parent_id, new_index) GraphEngine
        +get_node(node_id) BaseNode?
        +get_root() BasePipeline?
        +find_node(node_id) BaseNode?
        +validate(pipeline) tuple
        +execute(pipeline, context) GraphEngine
        +mutate(pipeline, context) GraphEngine
        +notify_changed(node, old_state, new_state, context) GraphEngine
        +simplify(pipeline) BasePipeline
        +expand(node, children) BasePipeline
        +collapse(node) BaseNode
        +replace_node(old_node, new_node) BaseNode
        +register_plugin(plugin) GraphEngine
        +get_extension(name) Callable?
        +get_plugin(name) BasePlugin?
        +get_event_bus() EventBus
        +get_context() Context?
        +ready() GraphEngine
    }

    class GraphUtils {
        <<static / stateless>>
        +walk(root, mode, direction, stop_when)$ List
        +find_node(root, node_id)$ BaseNode?
        +get_root(node)$ BaseNode
        +get_flat_list(root)$ List
        +get_leaves(root)$ List
        +get_fqn(node)$ str
        +is_ancestor_of(ancestor, descendant)$ bool
        +get_depth(root)$ int
        +get_size(root)$ int
        +has_cycles(root)$ bool
        +detect_cycles(root)$ List
        +validate(root)$ tuple
    }

    class EventBus {
        -Dict~str,List~EventHandler~~ _subscribers
        -Deque~Event~ _history
        +subscribe(event_name, callback, priority, name) EventBus
        +unsubscribe(event_name, name) EventBus
        +emit(event_name, data, context) EventBus
        +emit_async(event_name, data, context) EventBus
        +get_history(limit) List~Event~
        +clear() EventBus
    }

    class EventDispatcher {
        -EventBus _event_bus
        -PluginRegistry _registry
        -_register_all_hooks()
        -_dispatch(event_name, hook_name, keys, use_bool, data, context)
        -_handle_plugin_error(data, context)
    }

    class PluginRegistry {
        -Dict~str,BasePlugin~ _plugins
        -Dict~str,Callable~ _extensions
        -bool _is_ready
        -List~str~ _quarantined
        +register(plugin) PluginRegistry
        +ready()
        +get_plugin(name) BasePlugin?
        +get_extension(name) Callable?
        +get_all_plugins() List~BasePlugin~
        +get_plugins_by_priority() List~BasePlugin~
        +call_hook(hook_name, args) List
        +call_hook_first(hook_name, args) Any
        +call_hook_bool(hook_name, args) bool
    }

    class ContextManager {
        -Context? _current_context
        +create(kwargs) Context
        +get_current() Context?
        +set_current(context)
        +clear()
        +has_current() bool
    }

    GraphEngine "1" *-- "1" PluginRegistry
    GraphEngine "1" *-- "1" EventBus
    GraphEngine "1" *-- "1" EventDispatcher
    GraphEngine "1" *-- "1" ContextManager
    GraphEngine ..> GraphUtils : использует (статические вызовы)
    GraphEngine "1" o-- "0..*" BaseNode : _nodes registry
    EventDispatcher "1" --> "1" EventBus : подписывается на события
    EventDispatcher "1" --> "1" PluginRegistry : call_hook / call_hook_bool
```

`*--` — композиция (жизненный цикл владеемого объекта совпадает с жизнью
`GraphEngine`: `PluginRegistry`/`EventBus`/`EventDispatcher`/
`ContextManager` создаются в `GraphEngine.__init__` и не разделяются между
разными `GraphEngine`). `o--` — агрегация: узлы в `_nodes` существуют и вне
контекста конкретного вызова (пользователь может держать ссылку на `node`
уже после того, как он удалён из `_nodes`).

## Плагины и здоровье

```mermaid
classDiagram
    class BasePlugin {
        <<abstract>>
        -PluginHealth _health
        +name : str*
        +version : str*
        +priority : int = 100
        +enabled : bool = True
        +is_quarantined() bool
        +error_count() int
        +reset_health()
        +on_register(registry)
        +on_ready()
        +on_unregister()
        +on_node_created(node, context)
        +on_node_added(pipeline, node, index, context)
        +on_node_removed(node, parents, context)
        +on_node_moved(node, old_parents, new_parent, context)
        +on_node_changed(node, old_state, new_state, context)
        +on_pipeline_created(pipeline, context)
        +on_pipeline_validated(pipeline, context) bool?
        +on_pipeline_executed(pipeline, context)
        +on_pipeline_mutated(pipeline, context)
        +on_graph_changed(pipeline, context)
        +on_cycle_detected(pipeline, cycle_nodes, context)
        +on_orphan_detected(node, context)
        +get_extensions() Dict
        +get_dependencies() List~str~
        +get_state() Dict
        +set_state(state)
        #_record_success()
        #_record_error(error)
    }

    class PluginHealth {
        +int error_count = 0
        +int max_errors = 3
        +bool is_quarantined = False
        +str? quarantine_reason
        +Exception? last_error
        +int success_count = 0
        +record_success()
        +record_error(error)
        +reset()
    }

    BasePlugin "1" *-- "1" PluginHealth : self._health
    PluginRegistry "1" o-- "0..*" BasePlugin : _plugins
```

`name`/`version` помечены `*` — абстрактные `@property`, обязательные к
переопределению у наследников. На практике (см. примеры и тесты) они
переопределяются как простые атрибуты класса — Python это допускает.

## Событийная модель

```mermaid
classDiagram
    class Event {
        <<dataclass>>
        +str name
        +Any data
        +Context? context
        +datetime timestamp
        +str? source
    }

    class EventHandler {
        <<dataclass>>
        +Callable callback
        +EventPriority priority
        +str name
    }

    class EventPriority {
        <<IntEnum>>
        CRITICAL = 0
        HIGH = 10
        NORMAL = 20
        LOW = 30
        LOWEST = 40
    }

    class Context {
        +str session_id
        +str? user_id
        +str? transaction_id
        +str? source
        +datetime timestamp
        +Dict metadata
    }

    EventBus "1" o-- "0..*" Event : _history
    EventBus "1" o-- "0..*" EventHandler : _subscribers
    EventHandler "1" --> "1" EventPriority
    Event "0..1" --> "0..1" Context
```
