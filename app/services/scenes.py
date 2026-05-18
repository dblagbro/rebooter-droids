"""Named device scenes — v0.5.92 (Stage C).

CRUD over the `scenes` table. A scene is a named bundle of per-device
target states; a watchdog `apply_scene` action references one by
`scene_id`. `validate_scene_items` is the canonical item-shape check —
reused by `services/watchdog.py` for an `apply_scene` action's inline
`items`. The runtime apply path is
`watchdog_runtime/_actions.py::_fire_scene`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import session_scope
from app.models import Scene

_MAX_ITEMS = 50
_RELAY_STATES = ("on", "off", "cycle")


class SceneError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def validate_scene_items(items, *, field: str = "items") -> list:
    """Validate + normalise a scene's item list — one entry per device,
    each carrying a relay state (`on`/`off`/`cycle`) and/or an
    `apply_config` payload. Returns the cleaned list; raises SceneError.
    """
    if not isinstance(items, list) or not items:
        raise SceneError("validation_failed", f"{field} must be a non-empty list")
    if len(items) > _MAX_ITEMS:
        raise SceneError(
            "validation_failed", f"{field} accepts at most {_MAX_ITEMS} entries"
        )
    cleaned: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise SceneError("validation_failed", f"{field}[{i}] must be an object")
        device_id = str(item.get("device_id") or "").strip()
        if not device_id:
            raise SceneError(
                "validation_failed", f"{field}[{i}].device_id is required"
            )
        relay = item.get("relay")
        config = item.get("config")
        if relay is not None and relay not in _RELAY_STATES:
            raise SceneError(
                "validation_failed",
                f"{field}[{i}].relay must be 'on', 'off' or 'cycle'",
            )
        if config is not None and not isinstance(config, dict):
            raise SceneError(
                "validation_failed", f"{field}[{i}].config must be an object"
            )
        if relay is None and not config:
            raise SceneError(
                "validation_failed",
                f"{field}[{i}] needs a 'relay' state or a 'config' payload",
            )
        entry: dict = {"device_id": device_id}
        if relay is not None:
            entry["relay"] = relay
        if config:
            entry["config"] = config
        cleaned.append(entry)
    return cleaned


def serialize_scene(s: Scene) -> dict:
    items = s.items or []
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "items": items,
        "device_count": len(items),
        "created_at": _iso(s.created_at),
        "updated_at": _iso(s.updated_at),
    }


def list_scenes() -> list[dict]:
    with session_scope() as session:
        return [
            serialize_scene(s)
            for s in session.scalars(select(Scene).order_by(Scene.name))
        ]


def get_scene(scene_id: str) -> dict | None:
    with session_scope() as session:
        s = session.get(Scene, scene_id)
        return serialize_scene(s) if s is not None else None


def scene_items(scene_id: str) -> list | None:
    """The raw item list for a scene, or None if the scene no longer
    exists. The `apply_scene` runtime resolves `scene_id` through here."""
    with session_scope() as session:
        s = session.get(Scene, scene_id)
        return list(s.items or []) if s is not None else None


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise SceneError("validation_failed", "name is required")
    if len(name) > 120:
        raise SceneError("validation_failed", "name must be 120 characters or fewer")
    return name


def create_scene(*, name: str, description: str | None, items) -> dict:
    name = _clean_name(name)
    cleaned = validate_scene_items(items)
    desc = (description or "").strip() or None
    scene = Scene(name=name, description=desc, items=cleaned)
    try:
        with session_scope() as session:
            session.add(scene)
            session.flush()
            return serialize_scene(scene)
    except IntegrityError:
        raise SceneError("name_conflict", f"a scene named '{name}' already exists")


def update_scene(scene_id: str, *, name: str, description: str | None,
                 items) -> dict | None:
    name = _clean_name(name)
    cleaned = validate_scene_items(items)
    desc = (description or "").strip() or None
    try:
        with session_scope() as session:
            s = session.get(Scene, scene_id)
            if s is None:
                return None
            s.name = name
            s.description = desc
            s.items = cleaned
            session.flush()
            return serialize_scene(s)
    except IntegrityError:
        raise SceneError("name_conflict", f"a scene named '{name}' already exists")


def delete_scene(scene_id: str) -> bool:
    with session_scope() as session:
        s = session.get(Scene, scene_id)
        if s is None:
            return False
        session.delete(s)
        session.flush()
        return True
