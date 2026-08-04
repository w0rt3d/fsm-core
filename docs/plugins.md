# Plugins (`PluginRegistry`, `BasePlugin`, `PluginHealth`)

## `BasePlugin`

Абстрактный базовый класс (`abc.ABC`). Обязательны только два абстрактных
свойства:

```python
@property
@abstractmethod
def name(self) -> str: ...

@property
@abstractmethod
def version(self) -> str: ...
```

На практике в коде (примеры, тесты) `name`/`version` переопределяются как
обычные class-level атрибуты (`name = "logging"`), что допустимо в Python
— атрибут класса удовлетворяет требованию абстрактного `@property`.

Необязательные для переопределения:

```python
@property
def priority(self) -> int:
    return 100

@property
def enabled(self) -> bool:
    return True
```

`priority` — чем меньше число, тем раньше плагин получает управление
(`PluginRegistry.get_plugins_by_priority` сортирует по возрастанию) — та
же схема «меньше = раньше», что и у `EventPriority`. `enabled` — плагин
можно временно выключить, переопределив это свойство динамическим
условием (например, читая feature-флаг), без вызова `unregister`
(которого в `PluginRegistry`, к слову, нет — см. ниже).

### Хуки жизненного цикла

```python
def on_register(self, registry): ...
def on_ready(self): ...
def on_unregister(self): ...
```

`on_register` вызывается синхронно внутри `PluginRegistry.register(...)`,
сразу после того как плагин добавлен в реестр. `on_ready` — при вызове
`registry.ready()` (который, в свою очередь, вызывается из
`GraphEngine.ready()`). **`on_unregister` объявлен, но в текущем коде
`PluginRegistry` нет метода `unregister` — этот хук физически не может
быть вызван библиотекой.** Это ещё одна зарезервированная, но не
реализованная точка расширения (см. `CHANGELOG.md`).

### Хуки графовых событий

Полный список, сигнатуры и то, какое событие `EventBus` их вызывает —
таблица в [events.md](events.md#полный-список-событий-публикуемых-fsm_core).
Все хуки — no-op по умолчанию (`...` в теле), кроме
`on_pipeline_validated`, у которого дефолтная реализация явно возвращает
`None`:

```python
def on_pipeline_validated(self, pipeline, context) -> Optional[bool]:
    return None
```

Это единственный хук с содержательным возвращаемым значением — остальные
хуки вызываются через `call_hook`, который собирает результаты, но никто
в `engine.py` их не читает; `on_pipeline_validated` вызывается через
`call_hook_bool`, но, как отмечено в
[execution_flow.md](execution_flow.md), даже его результат сейчас не
влияет на `GraphEngine.validate()`.

### Точки расширения

```python
def get_extensions(self) -> Dict[str, Callable]:
    return {}

def get_dependencies(self) -> List[str]:
    return []

def get_state(self) -> Dict[str, Any]:
    return {}

def set_state(self, state: Dict[str, Any]) -> None: ...
```

- **`get_extensions`** — реально используется: `PluginRegistry.register`
  копирует все пары `{имя: callable}` из `get_extensions()` в общий
  словарь `_extensions`, доступный через `GraphEngine.get_extension(name)`
  / `registry.get_extension(name)`. Это способ дать плагину зарегистрировать
  именованную функцию, доступную остальному коду напрямую, в обход системы
  хуков.
- **`get_dependencies`, `get_state`, `set_state`** — объявлены в
  интерфейсе `BasePlugin`, но **`PluginRegistry` их нигде не вызывает**.
  Нет проверки зависимостей между плагинами при регистрации или при
  вызове хуков, нет сохранения/восстановления состояния плагинов. Это
  задокументированные, но не реализованные части контракта — не
  полагайтесь на то, что `get_dependencies` как-то влияет на порядок
  вызова плагинов (единственное, что на это влияет, — `priority`).

## `PluginHealth` — circuit breaker

```python
class PluginHealth:
    def __init__(self, max_errors: int = 3):
        self.error_count = 0
        self.max_errors = max_errors
        self.is_quarantined = False
        self.quarantine_reason = None
        self.last_error = None
        self.success_count = 0
```

Каждый `BasePlugin` создаёт собственный `PluginHealth()` в `__init__`
(`self._health = PluginHealth()`) — `max_errors` для отдельного плагина
сейчас нельзя настроить через конструктор `BasePlugin` (он не принимает
параметров) — единственный способ изменить порог — переопределить
`__init__` плагина и создать `PluginHealth(max_errors=...)` вручную.

- `record_success()` — инкремент `success_count`. **Не сбрасывает
  `error_count`** — счётчик ошибок считает *общее* число ошибок за всё
  время жизни плагина, а не число ошибок *подряд*, несмотря на то, что
  комментарии в коде и README называют это «после N последовательных
  ошибок». По факту это накопительный счётчик: если плагин упал дважды,
  потом 100 раз отработал успешно, потом упал третий раз — `error_count`
  станет 3, и плагин уйдёт в карантин. Успешные вызовы между ошибками не
  «прощают» предыдущие ошибки.
- `record_error(error)` — инкремент `error_count`; при достижении
  `max_errors` — `is_quarantined = True` и текстовая причина в
  `quarantine_reason`.
- `reset()` — пересоздаёт `PluginHealth` с тем же `max_errors`, полностью
  сбрасывая счётчики и статус карантина.

## `PluginRegistry`

```python
_plugins: Dict[str, BasePlugin]     # name -> plugin
_extensions: Dict[str, Callable]    # extension name -> callable
_is_ready: bool
_quarantined: List[str]             # имена плагинов, ушедших в карантин
```

### `register(plugin)`

Регистрация по `plugin.name` как ключу. Повторная регистрация плагина с
уже занятым именем — не ошибка, а no-op с предупреждением в лог (плагин
не заменяется, `on_register` для дубликата не вызывается). После
добавления в `_plugins` вызывается `plugin.on_register(self)`, затем все
`get_extensions()` плагина копируются в общий `_extensions` (более поздний
плагин с тем же именем extension'а молча перезапишет более раннего —
`PluginRegistry` не проверяет коллизии имён extensions).

### `ready()`

Помечает `_is_ready = True`, вызывает `on_ready()` у **всех**
зарегистрированных плагинов, включая уже квалифицированные — то есть
даже плагин без текущих ошибок, если он всё равно неактивен по
`enabled=False`, всё ещё получит `on_ready()`; проверка `is_quarantined`/
`enabled` применяется только к графовым хукам через `_active_plugins()`,
не к `on_ready`. Ошибка внутри `on_ready` конкретного плагина логируется
и не прерывает вызов `on_ready` для остальных плагинов.

### Диспетчеризация хуков: `call_hook` / `call_hook_first` / `call_hook_bool`

Все три построены поверх общего генератора:

```python
def _active_plugins(self):
    for plugin in self.get_plugins_by_priority():
        if plugin.is_quarantined() or not plugin.enabled:
            continue
        yield plugin

def _resolved_hooks(self, hook_name):
    for plugin in self._active_plugins():
        method = getattr(plugin, hook_name, None)
        if method is not None:
            yield plugin, method
```

и общего `_call(plugin, method, args)`, который вызывает метод, при успехе
— `plugin._record_success()`, при исключении — `plugin._record_error(e)`,
логирует и пробрасывает исключение выше, где вызывающий код (`call_hook*`)
его всегда перехватывает и переходит к следующему плагину.

| Метод | Семантика | Где используется |
|---|---|---|
| `call_hook(hook_name, *args)` | Вызвать у всех активных плагинов, собрать все результаты в список | Все события `_HOOK_TABLE`, кроме `pipeline_validated` |
| `call_hook_first(hook_name, *args)` | Вызывать по очереди, вернуть первый **не-`None`** результат | Не используется `EventDispatcher` в текущем `_HOOK_TABLE`; доступен как публичный метод API |
| `call_hook_bool(hook_name, *args)` | Вызвать у всех, итог `False`, если хотя бы один вернул `False` | Только `on_pipeline_validated` |

Важно: если ровно один плагин упадёт на конкретном хуке, это **не**
прерывает вызов хука у следующих плагинов — исключение перехватывается
внутри цикла `call_hook*`, здоровье плагина обновляется, обход
продолжается со следующего плагина.

### Квалификация (карантин) плагина

Как только `plugin.is_quarantined()` становится `True` (внутри `_call`,
через `plugin._record_error`), `PluginRegistry` добавляет имя плагина в
`self._quarantined` (если его там ещё нет) и логирует предупреждение.
С этого момента `_active_plugins()` будет пропускать плагин при **любом**
следующем вызове `call_hook*` — включая хуки, отличные от того, на
котором произошёл карантин. Карантин — не «выключить этот конкретный
хук», а «выключить весь плагин целиком».

### Снятие плагина из карантина

```python
plugin.reset_health()
```

Вызывается на самом объекте плагина (`BasePlugin.reset_health()` →
`self._health.reset()`), не через `PluginRegistry`. `PluginRegistry` не
предоставляет собственного метода для сброса здоровья плагина по имени —
вызывающий код должен получить сам плагин (`engine.get_plugin(name)`) и
вызвать `reset_health()` на нём.

### Чего в `PluginRegistry` нет

- Нет `unregister(name)` — плагин, однажды зарегистрированный, остаётся в
  `_plugins` навсегда в рамках жизни данного `PluginRegistry`; временно
  выключить его можно только через `enabled = False` в самом плагине или
  доведя до карантина.
- Нет проверки версии/совместимости при регистрации — `version` плагина
  используется только в лог-сообщении `register`, ни на что не влияет
  программно.
- Нет разрешения зависимостей между плагинами (см. `get_dependencies`
  выше) и, соответственно, никакой топологической сортировки порядка
  вызова — порядок определяется исключительно `priority`.

## Как подключить свой плагин

```python
from fsm_core import BasePlugin, GraphEngine

class AuditPlugin(BasePlugin):
    name = "audit"
    version = "1.0"
    priority = 10  # выполнится раньше плагинов с priority=100 (по умолчанию)

    def get_extensions(self):
        return {"audit.count": self._count}

    def __init__(self):
        super().__init__()
        self._events = 0

    def _count(self):
        return self._events

    def on_node_added(self, pipeline, node, index, context):
        self._events += 1

engine = GraphEngine()
engine.register_plugin(AuditPlugin())
engine.ready()

# позже:
engine.get_extension("audit.count")()   # -> число событий node_added
```

`super().__init__()` в переопределённом `__init__` плагина обязателен —
он создаёт `self._health = PluginHealth()`; без него `_record_success`/
`_record_error` упадут с `AttributeError` при первом же вызове хука.
