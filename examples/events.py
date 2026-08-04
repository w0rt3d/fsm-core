"""
events.py — работа с EventBus напрямую: приоритеты обработчиков,
пользовательские (не встроенные) события, история событий, emit_async.

Запуск: python examples/events.py
"""
import asyncio

from fsm_core import EventBus, EventPriority, GraphEngine


def sync_example() -> None:
    bus = EventBus(max_history=50)

    # Меньший приоритет -> обработчик вызывается раньше.
    bus.subscribe("ping", lambda data, ctx: print("  [HIGH] got", data), priority=EventPriority.HIGH)
    bus.subscribe("ping", lambda data, ctx: print("  [LOW] got", data), priority=EventPriority.LOW)

    print("emit('ping'):")
    bus.emit("ping", data={"n": 1})

    # Пользовательское событие: EventBus не ограничен набором встроенных
    # имён событий GraphEngine. Такое событие не пройдёт через
    # EventDispatcher/систему плагинов -- только через прямых подписчиков.
    bus.subscribe("custom.metric", lambda data, ctx: print("  metric:", data))
    bus.emit("custom.metric", data={"latency_ms": 42})

    print("history length:", len(bus.get_history()))
    print("last event name:", bus.get_history(limit=1)[0].name)


async def async_example() -> None:
    bus = EventBus()

    async def async_handler(data, context):
        await asyncio.sleep(0)  # имитация асинхронной работы
        print("  async handler:", data)

    def sync_handler(data, context):
        print("  sync handler (running in a thread):", data)

    bus.subscribe("job.done", async_handler, priority=EventPriority.HIGH)
    bus.subscribe("job.done", sync_handler, priority=EventPriority.LOW)

    print("emit_async('job.done'):")
    await bus.emit_async("job.done", data={"job_id": "abc"})


def engine_events_example() -> None:
    # EventBus, встроенный в GraphEngine, доступен через get_event_bus() --
    # полезно, когда нужно подписаться на встроенные события без
    # написания отдельного плагина.
    engine = GraphEngine()
    engine.get_event_bus().subscribe(
        "node_created", lambda data, ctx: print("  node_created:", data["node"].id)
    )
    engine.create_node("standalone")


if __name__ == "__main__":
    print("--- sync ---")
    sync_example()
    print("--- async ---")
    asyncio.run(async_example())
    print("--- engine ---")
    engine_events_example()
