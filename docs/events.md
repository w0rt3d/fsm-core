# Events (`EventBus`)

## Модель

`EventBus` — приоритетный pub/sub с ограниченной по размеру историей.
Ядро — три структуры:

```python
_subscribers: Dict[str, List[EventHandler]]   # имя события -> обработчики
_history: Deque[Event]                        # кольцевой буфер, maxlen=max_history
```

```python
@dataclass
class Event:
    name: str
    data: Any
    context: Optional[Context]
    timestamp: datetime            # default_factory=datetime.now
    source: Optional[str] = None   # нигде в коде не устанавливается

@dataclass
class EventHandler:
    callback: Callable
    priority: EventPriority
    name: str
```

`Event.source` объявлен, но нигде в `fsm_core` не присваивается —
`EventBus.emit`/`emit_async` создают `Event` без аргумента `source`, так
что для всех событий, публикуемых самой библиотекой, это поле всегда
`None`. Поле доступно вызывающему коду, который эмитит события напрямую
через `event_bus.emit(...)`, но `emit` не принимает `source` как параметр
— установить его можно только сконструировав `Event` вручную и обходя
`emit`, что выходит за пределы штатного использования `EventBus`.

## Приоритеты

```python
class EventPriority(IntEnum):
    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30
    LOWEST = 40
```

Меньшее значение — выше приоритет, выполняется раньше. Обработчики одного
события хранятся отсортированными по `priority` (`handlers.sort(key=lambda
h: h.priority)` при каждом `subscribe`) — сортировка происходит на этапе
подписки, не на этапе `emit`, то есть стоимость сортировки платится один
раз при добавлении обработчика, а не при каждой публикации события. При
равном приоритете порядок между обработчиками — порядок добавления в
список до сортировки (сортировка Python — стабильная).

## `subscribe(event_name, callback, priority=NORMAL, name="")`

- `name` по умолчанию берётся из `callback.__name__`, если явно не задано.
- Повторная подписка **того же** `callback` с **тем же** `name` на то же
  событие — не ошибка, а no-op с предупреждением в лог (дубликат не
  добавляется).
- Один и тот же `callback`, подписанный с разными `name`, — это два разных
  обработчика с точки зрения `EventBus`.

## `unsubscribe(event_name, name)`

Ищет обработчик по `name` (не по `callback` — то есть отписаться можно,
зная только имя, без ссылки на исходную функцию). Поднимает
`HandlerNotFoundError`, если события с таким именем нет вообще, или если
обработчик с таким `name` для него не найден — в отличие от `subscribe`,
здесь ошибка не проглатывается.

## `emit(event_name, data, context=None)`

1. Создаёт `Event`, добавляет его в историю (**всегда**, даже если
   подписчиков нет).
2. Если подписчиков нет — просто выходит (это не ошибка).
3. Вызывает `handler.callback(data, context)` для каждого обработчика **по
   порядку приоритета**, синхронно, один за другим.
4. **Падение одного обработчика не останавливает остальные.** Исключение
   ловится, логируется (`exc_info=True`), обработчик считается «упавшим» —
   но следующий обработчик всё равно получает управление. `emit` сам по
   себе не поднимает исключение обработчика наружу.

Это ключевое свойство для системы плагинов: `EventDispatcher`
подписывается на события как обычный обработчик, и падение конкретного
хука конкретного плагина внутри `PluginRegistry.call_hook` (см.
`plugins.md`) изолировано на уровне `PluginRegistry`, а падение самого
`EventDispatcher`-обработчика (маловероятное, но теоретически возможное)
изолировано ещё и на уровне `EventBus.emit`.

## `emit_async(event_name, data, context=None)`

Асинхронный аналог. Все обработчики запускаются **конкурентно**
(`asyncio.gather(..., return_exceptions=True)`), а не последовательно:

```python
async def run(h):
    if inspect.iscoroutinefunction(h.callback):
        return await h.callback(data, context)
    return await asyncio.to_thread(h.callback, data, context)
```

Синхронные обработчики выполняются в отдельном потоке через
`asyncio.to_thread`, чтобы не блокировать event loop. **Важное отличие от
`emit`**: порядок выполнения по приоритету здесь не гарантирует порядок
*завершения* — обработчики стартуют в порядке приоритета, но
выполняются параллельно, поэтому более низкоприоритетный обработчик
может фактически завершиться раньше высокоприоритетного, если он
быстрее. Приоритет в `emit_async` влияет на порядок *запуска*, не на
порядок *эффекта*. Если между обработчиками одного события есть
зависимость по порядку эффектов — нужен `emit` (синхронный), не
`emit_async`.

## `get_history(limit=None)`

Возвращает список событий из кольцевого буфера. `limit=None` — вся
доступная история (до `max_history` последних событий — более старые уже
вытеснены `deque(maxlen=...)`). `limit=0` — пустой список (см. запись в
`CHANGELOG.md` про исправленный срез `[-0:]`). Положительный `limit` —
последние `limit` записей.

## `max_history`

Задаётся при создании `GraphEngine(max_history=1000)` (проброс до
`EventBus(max_history=...)`). Это **не** ограничение на число подписок или
число публикуемых событий — только на длину хранимой истории; старые
записи молча вытесняются, `emit` не выдаёт предупреждения при
переполнении.

## Полный список событий, публикуемых `fsm_core`

| Событие | Кто публикует | Данные (`data`) |
|---|---|---|
| `node_created` | `GraphEngine.create_node` | `{"node": BaseNode}` |
| `pipeline_created` | `GraphEngine.create_pipeline` | `{"pipeline": BasePipeline}` |
| `node_added` | `GraphEngine.add_node` | `{"pipeline", "node", "index"}` |
| `node_removed` | `GraphEngine.remove_node` | `{"node", "parents"}` (список родителей) |
| `node_moved` | `GraphEngine.move_node` | `{"node", "old_parents", "new_parent"}` |
| `pipeline_validated` | `GraphEngine.validate` | `{"pipeline", "is_valid", "cycle_nodes"}` |
| `cycle_detected` | `GraphEngine.validate` (если есть цикл) | `{"pipeline", "cycle_nodes"}` |
| `pipeline_executed` | `GraphEngine.execute` | `{"pipeline"}`, с `context` |
| `pipeline_mutated` | `GraphEngine.mutate` | `{"pipeline"}`, с `context` |
| `graph_changed` | `mutate`, `simplify`, `expand`, `collapse`, `replace_node` | `{"pipeline"}` |
| `node_changed` | `GraphEngine.notify_changed` | `{"node", "old_state", "new_state"}`, с `context` |

### Объявленные, но не публикуемые события

`EventDispatcher` подписывается на `"orphan_detected"` и на
`"plugin_error"` (см. `dispatcher.py`), а `BasePlugin` объявляет хук
`on_orphan_detected`. **В текущем коде библиотеки ничто не вызывает
`event_bus.emit("orphan_detected", ...)` или `event_bus.emit("plugin_error",
...)`.** Это зарезервированные точки расширения: инфраструктура для их
обработки существует и готова, но нет ни одного «продюсера» события внутри
`fsm_core`. Если вы хотите их использовать — вам нужно эмитить их из
собственного кода (например, плагина, который сам обнаруживает
сироты-узлы) — библиотека это не делает автоматически.

## Как подписаться на своё событие

`EventBus` не ограничивает набор имён событий — это просто строка-ключ
словаря. Подписка на пользовательское событие ничем не отличается от
подписки на встроенное:

```python
def on_custom(data, context):
    print("custom event:", data)

engine.get_event_bus().subscribe("my_custom_event", on_custom, name="custom_logger")
engine.get_event_bus().emit("my_custom_event", data={"foo": "bar"})
```

Такое событие не пройдёт через `EventDispatcher` (он реагирует только на
имена из `_HOOK_TABLE`) и не вызовет никаких хуков `BasePlugin` — оно
останется на уровне прямых подписчиков `EventBus`, если вы явно не
добавите обработку в `_HOOK_TABLE` (потребует правки `dispatcher.py`,
поскольку таблица не расширяется извне пакета).
