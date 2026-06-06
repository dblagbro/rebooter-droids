"""In-process pub/sub event bus for SSE broadcasts (0.6.21, #178 Phase 1).

The hub fires events (e.g. "command queued for device X") that subscribed
SSE clients — currently the LAN relay agent + admin browser tabs — pick
up in real time. This lets a sub-200ms control loop run without polling:

    operator click → POST /admin/devices/<id>/commands
        → enqueue_for_device publishes to bus
            → SSE stream delivers event to LAN agent
                → agent POSTs http://<device-ip>/api/relay/<on|off>
                    → relay flips (~100-300ms end-to-end on LAN)

Design constraints:
- Single gunicorn worker (per gunicorn.conf.py), so an in-process queue is
  sufficient — no Redis / external broker needed yet. Move to one if a
  second worker ever appears.
- Subscribers are tracked by weakref so a disconnecting SSE client doesn't
  pin memory if the dispatching thread races a close.
- Per-subscriber bounded queue (default 100) — if a slow consumer falls
  behind, drop NEW events for them (preserve their existing backlog so
  they still see *something*; better than blocking the publisher thread).
"""
from __future__ import annotations

import logging
import queue
import threading
import weakref

log = logging.getLogger(__name__)

_DEFAULT_QUEUE_SIZE = 100


class Subscriber:
    """One SSE consumer. `queue` carries dict payloads; close() unblocks
    a waiting iterator so the SSE generator can exit cleanly."""

    # 0.6.22 bugfix: __weakref__ must be in __slots__ for weakref.ref()
    # to work — the bus tracks subscribers via weak refs so a dropped
    # SSE client doesn't pin memory.
    __slots__ = ("queue", "_closed", "__weakref__")

    def __init__(self, max_size: int = _DEFAULT_QUEUE_SIZE) -> None:
        self.queue: "queue.Queue[dict | None]" = queue.Queue(maxsize=max_size)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.queue.put_nowait(None)  # sentinel
        except queue.Full:
            pass


_subscribers: "set[weakref.ReferenceType[Subscriber]]" = set()
_lock = threading.Lock()


def subscribe(max_size: int = _DEFAULT_QUEUE_SIZE) -> Subscriber:
    sub = Subscriber(max_size=max_size)
    with _lock:
        _subscribers.add(weakref.ref(sub))
    return sub


def unsubscribe(sub: Subscriber) -> None:
    sub.close()
    # Dead refs are pruned in publish(); explicit removal is best-effort.


def publish(event: dict) -> None:
    """Fan event out to all live subscribers. Never blocks the caller;
    full queues drop the event for that one subscriber."""
    dead: set = set()
    with _lock:
        refs = list(_subscribers)
    for ref in refs:
        sub = ref()
        if sub is None or sub._closed:
            dead.add(ref)
            continue
        try:
            sub.queue.put_nowait(event)
        except queue.Full:
            log.warning("event_bus: dropping event for slow subscriber (queue full)")
    if dead:
        with _lock:
            _subscribers.difference_update(dead)
