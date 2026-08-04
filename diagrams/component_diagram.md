# Component Diagram (C4 — Level 2: Container/Component)

## Контекст (C4 Level 1)

```mermaid
graph TB
    User["Разработчик, использующий библиотеку<br/>(человек)"]
    Lib["fsm_core<br/>(Python-библиотека, in-process)"]

    User -->|"import fsm_core;<br/>наследует BaseNode/BasePlugin;<br/>вызывает GraphEngine"| Lib
```

`fsm_core` — не сервис, не отдельный процесс: это библиотека, встраиваемая
в процесс потребителя. C4 «Level 1: System Context» здесь вырожден до
одного actor'а и одной системы — внешних систем, с которыми `fsm_core`
взаимодействовал бы по сети/API, в коде нет (нет HTTP-клиентов, нет работы
с БД, нет очередей сообщений).

## Компоненты (C4 Level 3)

```mermaid
graph TB
    subgraph "Клиентский код (за пределами fsm_core)"
        App["Приложение"]
        UserPlugin["Плагины пользователя<br/>(наследники BasePlugin)"]
        UserNode["Пользовательские узлы<br/>(наследники BaseNode/BasePipeline, при необходимости)"]
    end

    subgraph "fsm_core — публичный API"
        Engine["GraphEngine<br/>[Facade]<br/>engine.py"]
        Node["BaseNode / BasePipeline<br/>[Data]<br/>node.py"]
        Utils["GraphUtils<br/>[Stateless algorithms]<br/>graph_utils.py"]
        Bus["EventBus<br/>[Pub/Sub]<br/>event.py"]
        Registry["PluginRegistry / BasePlugin / PluginHealth<br/>[Plugin lifecycle]<br/>plugins.py"]
        Ctx["Context / ContextManager<br/>[Ambient data]<br/>context.py"]
    end

    subgraph "fsm_core — внутренние компоненты"
        Dispatcher["EventDispatcher<br/>[Bridge]<br/>dispatcher.py"]
        Errors["wrap_errors<br/>[Decorator]<br/>_errors.py"]
        Exceptions["Exception hierarchy<br/>exceptions.py"]
    end

    App -->|"create_node, add_node,<br/>execute, register_plugin"| Engine
    UserPlugin -.->|"наследует"| Registry
    UserNode -.->|"наследует (не обязательно)"| Node

    Engine --> Node
    Engine --> Utils
    Engine --> Bus
    Engine --> Registry
    Engine --> Ctx
    Engine -->|"создаёт в __init__"| Dispatcher

    Dispatcher -->|"subscribe(event_name, handler)"| Bus
    Dispatcher -->|"call_hook / call_hook_bool"| Registry

    Node -->|"is_ancestor_of()"| Utils

    Engine -.-> Errors
    Node -.-> Errors
    Utils -.-> Errors
    Bus -.-> Errors
    Registry -.-> Errors
    Errors -.-> Exceptions

    Registry -.->|"вызывает хуки на"| UserPlugin
```

### Легенда

- **Сплошная стрелка** — прямой вызов метода / импорт.
- **Пунктирная стрелка** — отношение наследования или использование
  декоратора/иерархии исключений (не вызов конкретного метода в моменте).
- `[Facade]`, `[Data]`, `[Stateless algorithms]`, `[Pub/Sub]`, `[Plugin
  lifecycle]`, `[Ambient data]`, `[Bridge]`, `[Decorator]` — архитектурная
  роль компонента, не тип из UML.

### Кто снаружи, кто внутри

Публичный API (`fsm_core/__init__.py`) экспортирует: `GraphEngine`,
`BaseNode`/`BasePipeline`, `GraphUtils`, `EventBus`/`Event`/
`EventHandler`/`EventPriority`, `BasePlugin`/`PluginRegistry`/
`PluginHealth`, `Context`/`ContextManager`. `EventDispatcher`,
`wrap_errors` и внутренние классы `_Validate` каждого модуля — не
экспортируются и не задуманы для прямого использования потребителем
библиотеки (см. `docs/architecture.md`).
