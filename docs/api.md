# API Reference

Справочник по публичному API — всему, что экспортируется из
`fsm_core/__init__.py`:

```python
__all__ = [
    "BaseNode", "BasePipeline", "GraphEngine",
    "EventBus", "Event", "EventHandler", "EventPriority",
    "BasePlugin", "PluginRegistry", "PluginHealth",
    "Context", "ContextManager", "GraphUtils",
]
```

Все исключения, которые библиотека поднимает, — в
[exceptions.py](../fsm_core/exceptions.py); иерархия приведена в конце
этого файла.

---

## `BaseNode`

Узел графа. pydantic-модель (`model_config = {"arbitrary_types_allowed":
True}`).

**Поля**: `id: str`, `type: Literal["node"]`, `parents: List[BaseNode]`
(`exclude=True`), `children: List[BaseNode]` (`exclude=True`),
`extensions: Dict[str, Any]`, `metadata: Dict[str, Any]`.

| Метод | Параметры | Возврат | Исключения |
|---|---|---|---|
| `__init__` | `**data` (поля pydantic) | — | `InvalidOperationError`, если `id` пустой/только пробелы |
| `add_child(child, index=None)` | `child: BaseNode`, `index: Optional[int]` | `self` (для чейнинга) | `InvalidOperationError` (не `BaseNode`, узел сам себе ребёнок), `DuplicateNodeError` (уже есть), `CycleDetectedError` (создаст цикл) |
| `remove_child(child_id)` | `child_id: str` | Удалённый `BaseNode` или `None` | `InvalidOperationError` при внутренней ошибке |
| `add_parent(parent)` | `parent: BaseNode` | `self` | Аналогично `add_child` |
| `remove_parent(parent_id)` | `parent_id: str` | Удалённый `BaseNode` или `None` | `InvalidOperationError` |
| `clone()` | — | Глубокая копия узла (`model_copy(deep=True)`) — включая поддерево `children` | `InvalidOperationError` |
| `__eq__(other)` | — | `True`, если `other` — `BaseNode` с тем же `id` | — |
| `__hash__()` | — | `hash(self.id)` | — |

```python
from fsm_core import BaseNode

a = BaseNode(id="a")
b = BaseNode(id="b")
a.add_child(b)
assert b in a.children
assert a in b.parents
```

## `BasePipeline` (наследует `BaseNode`)

`type: Literal["pipeline"] = "pipeline"`. Не добавляет новых полей —
только читаемые алиасы поверх `BaseNode`:

| Метод | Эквивалент |
|---|---|
| `add_task(task, index=None)` | `self.add_child(task, index)`, возвращает `self` |
| `remove_task(task_id)` | `self.remove_child(task_id)` |
| `find_task(task_id)` | `GraphUtils.find_node(self, task_id)` |
| `get_tasks()` | `self.children` |

## `GraphEngine`

Фасад: единственный объект, с которым в норме работает потребитель
библиотеки.

```python
GraphEngine(max_history: int = 1000)
```

### Создание узлов и структуры

| Метод | Параметры | Возврат | Исключения | Публикует событие |
|---|---|---|---|---|
| `create_node(node_id=None)` | `Optional[str]` | `BaseNode` | `InvalidOperationError` | `node_created` |
| `create_pipeline(pipeline_id=None)` | `Optional[str]` | `BasePipeline`. **Первый вызов становится корнем графа** | `InvalidOperationError` | `pipeline_created` |
| `add_node(pipeline, node, index=None)` | `BasePipeline, BaseNode, Optional[int]` | `self` | `InvalidOperationError`, `GraphError`-подклассы из `add_child` | `node_added` |
| `remove_node(node_id)` | `str` | Удалённый `BaseNode` или `None` | `InvalidOperationError`, `GraphError` | `node_removed` |
| `move_node(node_id, new_parent_id, new_index=None)` | `str, str, Optional[int]` | `self` | `InvalidOperationError` (граф без корня, узел/родитель не найден), `GraphError` | `node_moved` |

### Поиск и чтение

| Метод | Возврат | Заметки |
|---|---|---|
| `get_node(node_id)` | `Optional[BaseNode]` | O(1), из `self._nodes` — всех когда-либо созданных через `create_*` узлов |
| `get_root()` | `Optional[BasePipeline]` | Первый созданный пайплайн |
| `get_flat_list()` | `List[BaseNode]` | DFS от корня; `[]`, если корня нет |
| `get_fqn(node_id)` | `Optional[str]` | `"root/.../node_id"` через `parents[0]` на каждом уровне |
| `find_node(node_id)` | `Optional[BaseNode]` | DFS от корня; `None`, если корня нет или узел не присоединён к дереву |

### Валидация и «выполнение»

| Метод | Параметры | Возврат | Публикует |
|---|---|---|---|
| `validate(pipeline=None)` | `Optional[BasePipeline]` (иначе — корень) | `(is_valid: bool, cycle_nodes: List[BaseNode])` | `pipeline_validated`, и `cycle_detected`, если есть цикл |
| `execute(pipeline, context=None)` | — | `self` | `pipeline_executed`. Поднимает `InvalidOperationError`, если граф с циклом |
| `mutate(pipeline, context=None)` | — | `self` | `pipeline_mutated`, `graph_changed` |
| `notify_changed(node, old_state, new_state, context=None)` | `old_state/new_state: Dict[str, Any]` — передаются вызывающим кодом как есть | `self` | `node_changed` |

### Структурные перестройки

| Метод | Что делает | Возврат | Публикует |
|---|---|---|---|
| `simplify(pipeline)` | Рекурсивно (итеративно, снизу вверх) убирает пустые пайплайны, схлопывает пайплайны с единственным ребёнком | Изменённый корень поддерева | `graph_changed` |
| `expand(node, children)` | Заменяет лист новым `BasePipeline` (тот же `id`, что у `node`) с указанными `children`, перевешивая старые связи `node`. **Известная особенность**: результирующий `pipeline` не остаётся в реестре `self._nodes` (см. `internals.md`), поэтому `get_node(node_id)` после `expand()` вернёт `None`, хотя узел графически существует — используйте `find_node`/обход, а не `get_node`, для проверки после `expand()` | Новый `BasePipeline` | `graph_changed` |
| `collapse(node)` | Сворачивает всё поддерево `node` в сам `node` (итеративно, post-order) — потомки удаляются из графа и из `self._nodes` | `node` | `graph_changed` |
| `replace_node(old_node, new_node)` | Переносит все связи `old_node` на `new_node`, удаляет `old_node` из `self._nodes` | `new_node` | `graph_changed` |

Подробное пошаговое поведение — [internals.md](internals.md).

### Плагины / контекст / шина событий

| Метод | Возврат |
|---|---|
| `register_plugin(plugin)` | `self` |
| `get_extension(name)` | `Optional[Callable]` |
| `get_plugin(name)` | `Optional[BasePlugin]` |
| `get_event_bus()` | `EventBus` (внутренний, доступ для прямых подписок) |
| `get_context()` | `Optional[Context]` — текущий контекст `ContextManager` |
| `ready()` | `self` — вызывает `PluginRegistry.ready()` |

```python
from fsm_core import GraphEngine

engine = GraphEngine()
root = engine.create_pipeline("root")
node = engine.create_node("task")
engine.add_node(root, node)
is_valid, _ = engine.validate()
engine.execute(root)
```

---

## `GraphUtils`

Класс статических методов над объектами с полями `id/children/parents`
(типизирован как `BaseNode`, но фактически работает с любым объектом
такой формы — см. `tests/test_graph_utils.py`).

| Метод | Сигнатура | Возврат |
|---|---|---|
| `walk` | `(root, mode="dfs", direction="children", stop_when=None)` | `List[BaseNode]` |
| `find_node` | `(root, node_id)` | `Optional[BaseNode]` (не поднимает исключение на невалидный `node_id` — логирует и возвращает `None`) |
| `get_root` | `(node)` | `BaseNode` — идёт через `parents[0]` |
| `get_flat_list` | `(root)` | `List[BaseNode]` — `walk(root, "dfs", "children")` |
| `get_leaves` | `(root)` | `List[BaseNode]` — узлы без `children` |
| `get_fqn` | `(node)` | `str` — путь через `parents[0]` на каждом уровне |
| `is_ancestor_of` | `(ancestor, descendant)` | `bool` |
| `get_depth` | `(root)` | `int` — **только по ветке `children[0]`**, не максимум по всем веткам (см. `traversal.md`) |
| `get_size` | `(root)` | `int` — `len(get_flat_list(root))` |
| `has_cycles` | `(root)` | `bool` |
| `detect_cycles` | `(root)` | `List[BaseNode]` — полный путь цикла, `[]` если циклов нет |
| `validate` | `(root)` | `(bool, List[BaseNode])` |

Исключения: большинство методов — `GraphError` при неожиданной внутренней
ошибке (через `wrap_errors`). `walk` дополнительно поднимает
`InvalidOperationError` (подкласс `GraphError`) на некорректные
`mode`/`direction`/`stop_when`.

---

## `EventBus`

```python
EventBus(max_history: int = 1000)
```

| Метод | Параметры | Возврат | Исключения |
|---|---|---|---|
| `subscribe(event_name, callback, priority=NORMAL, name="")` | — | `self` | `InvalidEventNameError`, `InvalidHandlerNameError`, `InvalidCallbackError`, `InvalidPriorityError` |
| `unsubscribe(event_name, name)` | — | `self` | `InvalidEventNameError`, `InvalidHandlerNameError`, `HandlerNotFoundError` |
| `emit(event_name, data, context=None)` | Синхронно, по приоритету, падение обработчика не прерывает остальные | `self` | `InvalidEventNameError` |
| `emit_async(event_name, data, context=None)` | Конкурентно (`asyncio.gather`), sync-колбэки — через `asyncio.to_thread` | `self` (awaitable) | `InvalidEventNameError` |
| `get_history(limit=None)` | `Optional[int]` | `List[Event]` | `InvalidLimitError` (не int / отрицательный) |
| `clear()` | — | `self` | — |

## `Event` (dataclass)

`name: str`, `data: Any`, `context: Optional[Context]`,
`timestamp: datetime` (авто), `source: Optional[str] = None`
(библиотекой никогда не устанавливается).

## `EventHandler` (dataclass)

`callback: Callable`, `priority: EventPriority`, `name: str`.

## `EventPriority` (IntEnum)

`CRITICAL = 0`, `HIGH = 10`, `NORMAL = 20`, `LOW = 30`, `LOWEST = 40`.
Меньше — раньше.

```python
from fsm_core import EventBus, EventPriority

bus = EventBus()
bus.subscribe("ping", lambda data, ctx: print("pong", data), priority=EventPriority.HIGH)
bus.emit("ping", data={"n": 1})
```

---

## `BasePlugin` (ABC)

Абстрактные: `name: str` (property), `version: str` (property).
Полный список хуков и точек расширения — [plugins.md](plugins.md).

| Метод | Возврат | Заметки |
|---|---|---|
| `is_quarantined()` | `bool` | |
| `error_count()` | `int` | Накопительный, не «подряд» (см. `plugins.md`) |
| `reset_health()` | `None` | Полный сброс `PluginHealth` |
| `get_extensions()` | `Dict[str, Callable]` | Реально используется `PluginRegistry.register` |
| `get_dependencies()` | `List[str]` | Объявлен, `PluginRegistry` не читает |
| `get_state()` / `set_state(state)` | `Dict[str, Any]` / `None` | Объявлены, `PluginRegistry` не читает/не вызывает |

## `PluginHealth`

```python
PluginHealth(max_errors: int = 3)
```

`record_success()`, `record_error(error)`, `reset()`. Публичные поля:
`error_count`, `max_errors`, `is_quarantined`, `quarantine_reason`,
`last_error`, `success_count`.

## `PluginRegistry`

| Метод | Возврат | Исключения |
|---|---|---|
| `register(plugin)` | `self` | `PluginError` при неожиданной ошибке |
| `ready()` | `None` | — (ошибки `on_ready` отдельных плагинов логируются, не пробрасываются) |
| `get_plugin(name)` | `Optional[BasePlugin]` | — |
| `get_extension(name)` | `Optional[Callable]` | — |
| `get_all_plugins()` | `List[BasePlugin]` | — |
| `get_plugins_by_priority()` | `List[BasePlugin]` | Сортировка по возрастанию `priority` |
| `call_hook(hook_name, *args)` | `List[Any]` | Вызывает у всех активных плагинов |
| `call_hook_first(hook_name, *args)` | `Any` | Первый не-`None` результат |
| `call_hook_bool(hook_name, *args)` | `bool` | `False`, если хоть один хук вернул `False` |

```python
from fsm_core import BasePlugin, PluginRegistry

class Noop(BasePlugin):
    name = "noop"
    version = "0.1"

registry = PluginRegistry()
registry.register(Noop())
registry.ready()
```

---

## `Context`

pydantic-модель: `session_id: str` (авто-`uuid4`), `user_id:
Optional[str]`, `transaction_id: Optional[str]`, `source: Optional[str]`,
`timestamp: datetime` (авто), `metadata: Dict[str, Any]` (авто `{}`).

## `ContextManager`

| Метод | Возврат |
|---|---|
| `create(**kwargs)` | `Context` — создаёт и делает текущим |
| `get_current()` | `Optional[Context]` |
| `set_current(context)` | `None` |
| `clear()` | `None` |
| `has_current()` | `bool` |

Не потокобезопасен (обычный атрибут экземпляра, не `contextvars`) — см.
[context.md](context.md) и [design_decisions.md](design_decisions.md).

---

## Иерархия исключений

```
CoreError
├── EventBusError
│   ├── InvalidEventNameError
│   ├── InvalidCallbackError
│   ├── InvalidPriorityError
│   ├── InvalidHandlerNameError
│   ├── InvalidLimitError
│   └── HandlerNotFoundError
├── GraphError
│   ├── InvalidOperationError
│   ├── DuplicateNodeError
│   └── CycleDetectedError
└── PluginError
```

Ловить всё сразу, не различая подтип — `except CoreError`.
