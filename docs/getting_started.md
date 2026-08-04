# Getting Started

## Установка

Пакет не оформлен как устанавливаемый дистрибутив (нет `setup.py`/
`pyproject.toml`), поэтому работа предполагается из корня репозитория, с
установленной зависимостью:

```bash
pip install -r requirements.txt   # pydantic>=2.0
```

Для тестов дополнительно:

```bash
pip install -r requirements-dev.txt   # pytest>=7.0
```

## Единственная точка входа: `GraphEngine`

Всё публичное взаимодействие с библиотекой в обычном сценарии идёт через
один объект:

```python
from fsm_core import GraphEngine

engine = GraphEngine()
```

`GraphEngine` внутри себя создаёт и связывает реестр узлов, шину событий,
диспетчер событий, реестр плагинов и менеджер контекста — по одному набору
на инстанс. Если вам нужны два независимых графа в одном процессе — это два
независимых `GraphEngine`, не два вызова какого-то глобального API.

## Первый граф

Граф в `fsm_core` строится из двух типов узлов:

- `BaseNode` — обычный узел;
- `BasePipeline` — узел-контейнер (тоже узел, `BasePipeline` наследует
  `BaseNode`), у которого есть удобные алиасы `add_task`/`get_tasks` поверх
  `add_child`/`children`.

```python
root = engine.create_pipeline("root")  # первый вызов create_pipeline
                                        # становится engine.get_root()

fetch = engine.create_node("fetch")
transform = engine.create_node("transform")
load = engine.create_node("load")

for node in (fetch, transform, load):
    engine.add_node(root, node)
```

`create_pipeline` без аргумента `pipeline_id` сгенерирует `id` через
`uuid4`. **Важный нюанс**: корнем графа (`engine.get_root()`) становится
именно *первый* созданный пайплайн — все последующие вызовы
`create_pipeline()` возвращают независимые, ещё ни к чему не подключённые
пайплайны, которые можно потом «вклеить» в граф через `add_node`. Это
поведение нигде не описано отдельно в исходном коде и обнаруживается
только чтением `engine.py` — здесь оно зафиксировано явно, чтобы не
переоткрывать его методом проб и ошибок.

## Валидация и «выполнение»

```python
is_valid, cycle_nodes = engine.validate()
assert is_valid

engine.execute(root)
```

Здесь важно не привнести ожидание из мира workflow-движков:
`engine.execute(root)` **не вызывает никакого кода**, связанного с узлами
`fetch`/`transform`/`load`. Он:

1. вызывает `validate()`, и если граф содержит цикл — поднимает
   `InvalidOperationError`, не публикуя `pipeline_executed`;
2. публикует событие `pipeline_executed` с данными `{"pipeline": root}`.

Всё, что должно реально «случиться» при выполнении пайплайна — задача
плагина, подписанного на `on_pipeline_executed`, либо кода, подписанного
напрямую на `EventBus`. Подробнее — в
[execution_flow.md](execution_flow.md).

## Первый плагин

```python
from fsm_core import BasePlugin

class LoggingPlugin(BasePlugin):
    name = "logging"
    version = "1.0"

    def on_node_added(self, pipeline, node, index, context):
        print(f"{node.id} -> {pipeline.id}")

engine.register_plugin(LoggingPlugin())
engine.ready()
```

`register_plugin` можно вызывать в любой момент, но `ready()` — это явная
граница «конфигурация закончена»: она вызывает `on_ready()` у каждого
зарегистрированного плагина. Плагины, зарегистрированные *после*
`ready()`, своего `on_ready()` не получат — регистрируйте их до вызова
`ready()`, если хук `on_ready` для вас значим.

## Чтение состояния графа

```python
engine.get_node("fetch")          # -> BaseNode | None, O(1) по словарю
engine.find_node("fetch")         # -> BaseNode | None, DFS от корня
engine.get_flat_list()            # -> List[BaseNode], DFS от корня
engine.get_fqn("fetch")           # -> "root/fetch"
root.get_tasks()                  # -> List[BaseNode], прямые дети
```

`get_node` и `find_node` — не одно и то же: `get_node` читает внутренний
словарь `engine._nodes` (все когда-либо созданные через `engine.create_*`
узлы, включая те, что ещё не подключены к дереву), тогда как `find_node`
обходит граф от `get_root()` — и вернёт `None` для узла, который существует,
но не присоединён к корневому пайплайну.

## Что дальше

- [architecture.md](architecture.md) — как компоненты связаны между собой.
- [graph_model.md](graph_model.md) — инварианты `BaseNode`/`BasePipeline`.
- [plugins.md](plugins.md) — полный жизненный цикл плагина и health/карантин.
- [events.md](events.md) — список событий, приоритеты, история, async emit.
