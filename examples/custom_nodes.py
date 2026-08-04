"""
custom_nodes.py — работа с метаданными узла и структурными перестройками
графа (expand / simplify / collapse / replace_node).

Запуск: python examples/custom_nodes.py
"""
from fsm_core import GraphEngine


def metadata_example() -> None:
    engine = GraphEngine()
    root = engine.create_pipeline("root")

    # BaseNode.metadata / .extensions — свободные dict-поля; библиотека
    # сама туда ничего не пишет и ничего оттуда не читает, это место для
    # пользовательских данных, привязанных к узлу.
    task = engine.create_node("send_email")
    task.metadata["retry_count"] = 0
    task.metadata["owner"] = "billing-team"
    engine.add_node(root, task)

    print("task.metadata:", task.metadata)

    # clone() — глубокая копия, включая поддерево children/parents.
    clone = task.clone()
    print("clone.id == task.id:", clone.id == task.id)
    print("clone is task:", clone is task)


def expand_example() -> None:
    """Заменить лист подграфом: узел "notify" разворачивается в пайплайн
    из трёх шагов, сохраняя связи с прежними родителями/детьми."""
    engine = GraphEngine()
    root = engine.create_pipeline("root")
    notify = engine.create_node("notify")
    engine.add_node(root, notify)

    email = engine.create_node("send_email")
    sms = engine.create_node("send_sms")
    push = engine.create_node("send_push")

    pipeline = engine.expand(notify, [email, sms, push])

    print("expanded pipeline.id == original node.id:", pipeline.id == "notify")
    print("expanded tasks:", [t.id for t in pipeline.get_tasks()])
    print("still attached to root:", pipeline in root.get_tasks())

    # Известная особенность engine.expand(): результирующий pipeline имеет
    # тот же id, что и заменённый узел, и после expand() пропадает из
    # реестра engine._nodes (см. docs/internals.md) -- граф при этом
    # остаётся корректным, найти узел можно через find_node(), но не
    # через get_node().
    print("get_node('notify') after expand:", engine.get_node("notify"))
    print("find_node('notify') after expand:", engine.find_node("notify"))


def simplify_example() -> None:
    """simplify() схлопывает пустые пайплайны и пайплайны с единственным
    ребёнком -- полезно после программной сборки графа, где часть веток
    может остаться вырожденной."""
    engine = GraphEngine()
    root = engine.create_pipeline("root")
    wrapper = engine.create_pipeline("wrapper")  # будет схлопнут: 1 ребёнок
    leaf = engine.create_node("leaf")

    engine.add_node(root, wrapper)
    engine.add_node(wrapper, leaf)

    engine.simplify(root)

    print("leaf now direct child of root:", leaf in root.get_tasks())
    print("wrapper still tracked:", engine.get_node("wrapper") is not None)


def collapse_example() -> None:
    """collapse() сворачивает всё поддерево узла в сам узел -- удобно,
    когда нужно "свернуть" детализацию для отображения на верхнем уровне,
    не теряя сам узел."""
    engine = GraphEngine()
    root = engine.create_pipeline("root")
    branch = engine.create_pipeline("branch")
    engine.add_node(root, branch)

    prev = branch
    for i in range(5):
        n = engine.create_node(f"step_{i}")
        engine.add_node(prev, n)
        prev = n

    print("size before collapse:", len(engine.get_flat_list()))
    engine.collapse(branch)
    print("size after collapse:", len(engine.get_flat_list()))
    print("branch has no tasks:", branch.get_tasks() == [])


if __name__ == "__main__":
    print("--- metadata ---")
    metadata_example()
    print("--- expand ---")
    expand_example()
    print("--- simplify ---")
    simplify_example()
    print("--- collapse ---")
    collapse_example()
