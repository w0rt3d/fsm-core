"""
basic_pipeline.py — минимальный пример: создать пайплайн, добавить задачи,
провалидировать граф на циклы, "выполнить" его.

Напоминание: engine.execute() не вызывает никакой пользовательской логики
сам по себе — он валидирует граф и публикует событие "pipeline_executed".
Здесь мы подписываемся на это событие напрямую через EventBus, без
плагинов, чтобы показать самый короткий путь "узнать, что execute()
произошёл".

Запуск: python examples/basic_pipeline.py
"""
from fsm_core import GraphEngine


def main() -> None:
    engine = GraphEngine()

    root = engine.create_pipeline("etl-root")
    fetch = engine.create_node("fetch")
    transform = engine.create_node("transform")
    load = engine.create_node("load")

    for node in (fetch, transform, load):
        engine.add_node(root, node)

    # Прямая подписка на EventBus, без системы плагинов.
    def on_executed(data, context):
        pipeline = data["pipeline"]
        print(f"pipeline_executed: {pipeline.id} (tasks={[t.id for t in pipeline.get_tasks()]})")

    engine.get_event_bus().subscribe("pipeline_executed", on_executed, name="print_executed")

    is_valid, cycle_nodes = engine.validate()
    print(f"valid={is_valid}, cycle_nodes={[n.id for n in cycle_nodes]}")

    engine.execute(root)

    # Пайплайн с корнем и без циклов: get_flat_list() отдаёт DFS-порядок.
    print("flat list:", [n.id for n in engine.get_flat_list()])
    print("fqn(load):", engine.get_fqn("load"))


if __name__ == "__main__":
    main()
