# Sequence Diagrams

## Инициализация `GraphEngine` и подключение плагина

```mermaid
sequenceDiagram
    participant App
    participant Engine as GraphEngine
    participant Bus as EventBus
    participant Reg as PluginRegistry
    participant Disp as EventDispatcher
    participant Plug as BasePlugin (пользовательский)

    App->>Engine: GraphEngine(max_history=1000)
    Engine->>Bus: EventBus(max_history)
    Engine->>Reg: PluginRegistry()
    Engine->>Disp: EventDispatcher(event_bus, registry)
    Disp->>Bus: subscribe(name, handler) для каждой записи _HOOK_TABLE
    Note over Bus,Disp: мост событие -> хук готов ещё до регистрации плагинов

    App->>Engine: register_plugin(Plug)
    Engine->>Reg: register(Plug)
    Reg->>Plug: on_register(registry)
    Reg->>Reg: _extensions.update(Plug.get_extensions())

    App->>Engine: ready()
    Engine->>Reg: ready()
    Reg->>Plug: on_ready()
```

## `add_node` — от вызова API до хука плагина

```mermaid
sequenceDiagram
    participant App
    participant Engine as GraphEngine
    participant Node as BasePipeline/BaseNode
    participant Utils as GraphUtils
    participant Bus as EventBus
    participant Disp as EventDispatcher
    participant Reg as PluginRegistry
    participant P1 as Plugin (priority=10)
    participant P2 as Plugin (priority=100, quarantined)

    App->>Engine: add_node(pipeline, node, index=None)
    Engine->>Node: pipeline.add_child(node, index)
    Node->>Utils: is_ancestor_of(node, pipeline)
    Utils-->>Node: False (нет цикла)
    Node->>Node: children.append(node); node.parents.append(pipeline)
    Node-->>Engine: OK
    Engine->>Engine: self._nodes[node.id] = node
    Engine->>Bus: emit("node_added", {"pipeline","node","index"})
    Bus->>Bus: history.append(event)
    Bus->>Disp: handler(data, context)  (единственный подписчик на "node_added")
    Disp->>Disp: разобрать data по ключам ("pipeline","node","index")
    Disp->>Reg: call_hook("on_node_added", pipeline, node, index, context)
    Reg->>Reg: get_plugins_by_priority() -> [P1(10), P2(100)]
    Reg->>P1: on_node_added(pipeline, node, index, context)
    P1-->>Reg: OK -> _record_success()
    Note over Reg,P2: P2 в карантине -> _active_plugins() его пропускает
    Reg-->>Disp: [результат P1]
    Disp-->>Bus: (return value отброшен)
    Bus-->>Engine: OK
    Engine-->>App: self
```

## `execute` — валидация, публикация, и то, что `execute` НЕ делает

```mermaid
sequenceDiagram
    participant App
    participant Engine as GraphEngine
    participant CtxMgr as ContextManager
    participant Utils as GraphUtils
    participant Bus as EventBus
    participant Disp as EventDispatcher
    participant Reg as PluginRegistry

    App->>Engine: execute(pipeline, context=None)
    Engine->>CtxMgr: get_current()
    CtxMgr-->>Engine: Context | None

    Engine->>Engine: validate(pipeline)
    Engine->>Utils: GraphUtils.validate(pipeline)
    Utils-->>Engine: (is_valid, cycle_nodes)
    Engine->>Bus: emit("pipeline_validated", {...})
    Bus->>Disp: handler(...)
    Disp->>Reg: call_hook_bool("on_pipeline_validated", pipeline, context)
    Note over Reg: результат call_hook_bool НИКЕМ не читается engine.py

    alt is_valid == False
        Engine->>Bus: emit("cycle_detected", {"pipeline","cycle_nodes"})
        Bus->>Disp: handler(...)
        Disp->>Reg: call_hook("on_cycle_detected", ...)
        Engine-->>App: raise InvalidOperationError
    else is_valid == True
        Engine->>Bus: emit("pipeline_executed", {"pipeline"}, ctx)
        Bus->>Disp: handler(...)
        Disp->>Reg: call_hook("on_pipeline_executed", pipeline, context)
        Note over Reg: реальная работа (если есть) — только здесь,<br/>внутри on_pipeline_executed плагинов
        Engine-->>App: self
    end
```

## `emit_async` — конкурентное выполнение обработчиков

```mermaid
sequenceDiagram
    participant App
    participant Bus as EventBus
    participant H1 as async handler (priority=HIGH)
    participant H2 as sync handler (priority=LOW)
    participant Loop as asyncio event loop

    App->>Bus: await emit_async("job.done", data)
    Bus->>Bus: history.append(event)
    par запуск в порядке приоритета, выполнение — конкурентно
        Bus->>H1: await callback(data, context)
        Bus->>Loop: asyncio.to_thread(H2.callback, data, context)
        Loop->>H2: callback(data, context)  (в отдельном потоке)
    end
    Note over H1,H2: H2 может завершиться раньше H1,<br/>несмотря на более низкий приоритет —<br/>приоритет влияет на порядок ЗАПУСКА, не завершения
    Bus->>Bus: asyncio.gather(..., return_exceptions=True)
    Bus-->>App: результат (исключения обработчиков не прерывают остальные)
```

## Circuit breaker: путь плагина от здорового состояния до карантина

```mermaid
sequenceDiagram
    participant Reg as PluginRegistry
    participant Plug as BasePlugin
    participant Health as PluginHealth (max_errors=3)

    Note over Reg,Health: Вызов 1 хука — падение
    Reg->>Plug: on_node_created(node, context)
    Plug-->>Reg: raise RuntimeError
    Reg->>Health: record_error(e)
    Health->>Health: error_count = 1 (< 3, не в карантине)

    Note over Reg,Health: Вызов 2 — падение
    Reg->>Plug: on_node_created(node, context)
    Plug-->>Reg: raise RuntimeError
    Reg->>Health: record_error(e)
    Health->>Health: error_count = 2 (< 3)

    Note over Reg,Health: Вызов 3 — падение, достигнут порог
    Reg->>Plug: on_node_created(node, context)
    Plug-->>Reg: raise RuntimeError
    Reg->>Health: record_error(e)
    Health->>Health: error_count = 3 >= max_errors -> is_quarantined = True
    Reg->>Reg: _quarantined.append(plugin.name)

    Note over Reg,Plug: Вызов 4 — плагин уже отфильтрован
    Reg->>Reg: _active_plugins() пропускает Plug (is_quarantined() == True)
    Note over Plug: on_node_created НЕ вызывается для этого и последующих событий
```

Полный разбор циклов вызовов — [../docs/execution_flow.md](../docs/execution_flow.md)
и [../docs/plugins.md](../docs/plugins.md).
