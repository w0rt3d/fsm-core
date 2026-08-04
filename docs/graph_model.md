# Graph Model

## `BaseNode`

`BaseNode` — pydantic-модель (`pydantic.BaseModel`), а не dataclass и не
обычный класс. Это значит, что все поля валидируются pydantic при
создании и присваивании, а не только объявлены как type hints.

Поля:

| Поле | Тип | По умолчанию | Заметки |
|---|---|---|---|
| `id` | `str` | `uuid4()` | Проверяется на непустоту в `__init__` (`InvalidOperationError`, если пустая строка/только пробелы) |
| `type` | `Literal["node"]` | `"node"` | У `BasePipeline` переопределён в `Literal["pipeline"]` — используется как дискриминатор при сериализации/десериализации |
| `parents` | `List[BaseNode]` | `[]` | `exclude=True` — не попадает в `model_dump()`/JSON |
| `children` | `List[BaseNode]` | `[]` | `exclude=True` — не попадает в `model_dump()`/JSON |
| `extensions` | `Dict[str, Any]` | `{}` | Свободное поле для расширений (что туда класть — решает вызывающий код; библиотека сама туда ничего не пишет) |
| `metadata` | `Dict[str, Any]` | `{}` | Свободное поле произвольных метаданных |

Дополнительно объявлены приватные атрибуты pydantic (`PrivateAttr`, не
поля модели): `_child_ids: Set[str]` и `_parent_ids: Set[str]` — теневые
индексы id-шников текущих детей/родителей, нужны для O(1)-проверки
дубликатов в `add_child`/`add_parent` вместо линейного поиска по списку.

### Почему граф — DAG, а не дерево

`parents` — список, а не одиночная опциональная ссылка. Узел в этой модели
может иметь несколько родителей одновременно (см. тест
`test_move_node_multiple_parents`, где узел `x` подключается сразу к `p1`
и `p2`). Это архитектурно значимое решение: если бы `parents` было
единственной ссылкой, никакая часть API (`is_ancestor_of`, обход в
направлении `"parents"`, `move_node`) не имела бы смысла в текущем виде.

### Равенство по идентификатору, а не по значению

`BaseNode.__eq__`/`__hash__` переопределены:

```python
def __eq__(self, other):
    return isinstance(other, BaseNode) and self.id == other.id

def __hash__(self):
    return hash(self.id)
```

Причина — не стилистическая. Дефолтное `pydantic.BaseModel.__eq__`
сравнивает *все* поля, включая `children`/`parents`. Поскольку эти поля
образуют перекрёстные ссылки (родитель хранит ссылку на ребёнка, ребёнок —
обратную ссылку на родителя), сравнение двух связанных узлов рекурсивно
уходит во весь связный компонент графа — и делает это заново на каждом
вложенном сравнении. Для графа с реальным циклом ссылок такое сравнение не
ограничено по глубине. Идентификация по `id` — это то, как остальной код
уже трактует узлы (`_child_ids`/`_parent_ids`, поиск по `node.id ==
target_id` везде в `graph_utils.py`), поэтому равенство просто приведено в
соответствие с уже существующей семантикой, а не изобретено заново. Это же
делает `list.remove(node)`, `node in some_list` и использование узла как
ключа `dict`/элемента `set` корректными и быстрыми.

### `clone()`

```python
def clone(self) -> BaseNode:
    return self.model_copy(deep=True)
```

Глубокая копия через pydantic. **Важно**: `parents`/`children` при этом
тоже копируются глубоко (`deep=True` не различает исключённые из
сериализации поля — `exclude=True` действует только на `model_dump`/JSON,
не на `model_copy`). Клон узла с непустыми `children` — это клон целого
поддерева, а не одного узла с пустыми списками. Если нужен именно один
узел без связей — их нужно очистить у клона вручную после копирования; в
текущем коде такого хелпера нет.

## Операции над узлом

Все мутирующие операции обёрнуты `@wrap_errors(InvalidOperationError,
passthrough=(GraphError,))`: любое неожиданное исключение внутри
превращается в `InvalidOperationError` с сохранением исходного через `raise
... from e`, а «свои» ошибки графа (`CycleDetectedError`,
`DuplicateNodeError`, уже вложенный `InvalidOperationError`) пробрасываются
как есть.

### `add_child(child, index=None)`

1. Валидация (`_Validate.add_child`): `child` — экземпляр `BaseNode`, не
   сам узел, не уже существующий ребёнок (`DuplicateNodeError`).
2. Проверка цикла: `GraphUtils.is_ancestor_of(child, self)` — «является ли
   `child` предком `self`»; если да — добавление `child` в `self.children`
   создало бы цикл, поднимается `CycleDetectedError`.
3. Вставка в `children` (по индексу или в конец), синхронное обновление
   `_child_ids`, а также обратной связи: `child.parents.append(self)` и
   `child._parent_ids.add(self.id)`.

`add_child` **симметрично обновляет обе стороны связи за один вызов** —
не нужно отдельно вызывать `child.add_parent(self)`. Аналогично
`add_parent(parent)` на самом узле обновляет `parent.children` с той же
стороны.

### `remove_child(child_id)` / `remove_parent(parent_id)`

Линейный поиск по списку `children`/`parents` (не по `_child_ids` — тот
используется только для проверки существования, не для получения самого
объекта). Симметрично отвязывает обратную ссылку. Возвращает удалённый
узел или `None`, если `child_id`/`parent_id` не найден — не поднимает
исключение на «не найдено».

### Валидация добавления (`_Validate` в `node.py`)

- Нельзя добавить не-`BaseNode` (`InvalidOperationError`).
- Нельзя добавить узел самому себе в родители/дети (`InvalidOperationError`).
- Нельзя добавить уже существующего ребёнка/родителя повторно
  (`DuplicateNodeError`) — проверяется через множество id, а не перебором
  списка, то есть O(1).

## `BasePipeline`

```python
class BasePipeline(BaseNode):
    type: Literal["pipeline"] = "pipeline"

    def add_task(self, task, index=None): ...   # -> self.add_child(task, index)
    def remove_task(self, task_id): ...          # -> self.remove_child(task_id)
    def find_task(self, task_id): ...            # -> GraphUtils.find_node(self, task_id)
    def get_tasks(self): ...                     # -> self.children
```

`BasePipeline` не добавляет новых полей и новой семантики графа — это
`BaseNode` с другим `type`-дискриминатором и словарём-синонимом методов
(`add_task`/`get_tasks` вместо `add_child`/`children`), предназначенным
для читаемости пользовательского кода («в пайплайн добавляются задачи», а
не «в узел добавляются дети»). С точки зрения `GraphUtils`, `EventBus` и
`PluginRegistry` `BasePipeline` — обычный узел графа; ничего в остальном
коде не проверяет `isinstance(x, BasePipeline)`, кроме `GraphEngine`
(корень графа обязан быть `BasePipeline` — см. `create_pipeline` и
`replace_node`).

## Инварианты, которые поддерживает эта модель

1. **Ацикличность** обеспечивается на этапе `add_child`/`add_parent`
   (проверка перед мутацией), но не гарантируется структурно — граф можно
   сделать циклическим, если мутировать `node.children`/`node.parents`
   напрямую, в обход `add_child`/`add_parent` (это обычные списки
   Python, ничего не мешает делать `node.children.append(other)` вручную).
   `GraphUtils.validate`/`has_cycles`/`detect_cycles` существуют именно
   потому, что превентивная проверка в `add_child` — не единственный путь
   получить цикл в этой модели.
2. **Двусторонняя согласованность parent/child** — до тех пор, пока связи
   создаются и разрываются через `add_child`/`remove_child`/
   `add_parent`/`remove_parent`, а не прямой мутацией списков.
3. **Единственный источник истины про существование узла** для кода,
   идущего через `GraphEngine`, — словарь `GraphEngine._nodes`. Узел,
   созданный напрямую как `BaseNode(id=...)` в обход `engine.create_node`,
   не появится в `_nodes` и не будет виден `engine.get_node`, хотя может
   быть частью графа, если его вручную присоединили через `add_child`
   (см. `test_replace_node_updates_root`, где `new_root =
   BasePipeline(id="new_root")` создаётся напрямую, а не через
   `engine.create_pipeline`).

## Ограничения модели

- Списки `children`/`parents` не имеют верхней границы и не проверяются на
  дублирующиеся *значения*, отличные от проверки по `id` — два разных
  объекта `BaseNode` с одинаковым `id`, оба добавленные в разные родительские
  узлы напрямую (в обход `add_child`), не будут обнаружены как конфликт, пока
  кто-то не попытается сравнить их или найти по `id`.
- Никакого автоматического каскадного удаления вниз по дереву при
  `remove_child` — удаляется только прямая связь; поддерево удалённого узла
  остаётся присоединённым к нему самому (просто больше не достижимо от
  прежнего родителя, если не достижимо и откуда-то ещё).
