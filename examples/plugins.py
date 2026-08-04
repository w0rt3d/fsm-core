"""
plugins.py — пример плагина: приоритет, расширение (get_extensions),
и демонстрация карантина после повторяющихся ошибок.

Запуск: python examples/plugins.py
"""
from fsm_core import BasePlugin, GraphEngine


class AuditPlugin(BasePlugin):
    """Считает количество добавленных узлов и публикует это через
    get_extensions(), чтобы остальной код мог прочитать счётчик, не
    обращаясь к самому плагину напрямую."""

    name = "audit"
    version = "1.0"
    priority = 10  # меньше 100 (дефолт) -> выполнится раньше плагинов с priority=100

    def __init__(self) -> None:
        super().__init__()  # обязателен: создаёт self._health (PluginHealth)
        self._added = 0

    def on_node_added(self, pipeline, node, index, context) -> None:
        self._added += 1

    def get_extensions(self):
        return {"audit.added_count": lambda: self._added}


class FlakyPlugin(BasePlugin):
    """Плагин, который всегда падает на on_node_created — демонстрация
    circuit breaker: после 3 (по умолчанию) подряд неуспешных вызовов
    хука плагин уходит в карантин и дальше пропускается."""

    name = "flaky"
    version = "0.1"

    def on_node_created(self, node, context) -> None:
        raise RuntimeError("simulated failure")


def main() -> None:
    engine = GraphEngine()
    engine.register_plugin(AuditPlugin())
    engine.register_plugin(FlakyPlugin())
    engine.ready()

    root = engine.create_pipeline("root")
    for i in range(5):
        node = engine.create_node(f"n{i}")
        engine.add_node(root, node)

    added_count = engine.get_extension("audit.added_count")()
    print(f"audit.added_count = {added_count}")

    flaky = engine.get_plugin("flaky")
    print(f"flaky.is_quarantined() = {flaky.is_quarantined()}")
    print(f"flaky.error_count() = {flaky.error_count()}")

    # Снять с карантина явно, если понадобится повторить попытку.
    flaky.reset_health()
    print(f"after reset_health(): is_quarantined = {flaky.is_quarantined()}")


if __name__ == "__main__":
    main()
