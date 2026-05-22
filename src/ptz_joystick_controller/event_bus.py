from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, DefaultDict
from uuid import uuid4


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


EventHandler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, list[EventHandler]] = defaultdict(list)
        self._global_subscribers: list[EventHandler] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        self._global_subscribers.append(handler)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        event = Event(type=event_type, payload=payload or {})
        for handler in tuple(self._subscribers.get(event_type, [])):
            handler(event)
        for handler in tuple(self._global_subscribers):
            handler(event)
        return event
