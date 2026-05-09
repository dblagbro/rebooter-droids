from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import session_scope
from app.models import Device, Group, GroupMembership


class DuplicateNameError(ValueError):
    pass


def serialize_group(g: Group, member_count: int = 0) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "site_id": g.site_id,
        "member_count": member_count,
        "created_at": g.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def list_groups() -> list[dict]:
    with session_scope() as session:
        groups = list(session.scalars(select(Group).order_by(Group.created_at.desc())))
        # Cheap N+1 — fine at this scale
        results = []
        for g in groups:
            count = session.scalar(
                select(GroupMembership.device_id)
                .where(GroupMembership.group_id == g.id)
                .with_only_columns(GroupMembership.group_id)
                .limit(1)
            )
            count_query = session.execute(
                select(GroupMembership.device_id).where(GroupMembership.group_id == g.id)
            ).all()
            results.append(serialize_group(g, member_count=len(count_query)))
        return results


def create_group(name: str, description: str | None, site_id: str | None) -> dict:
    g = Group(name=name, description=description, site_id=site_id)
    try:
        with session_scope() as session:
            session.add(g)
            session.flush()
            return serialize_group(g, member_count=0)
    except IntegrityError:
        raise DuplicateNameError(f"a group named '{name}' already exists")


def get_group_detail(group_id: str) -> dict | None:
    with session_scope() as session:
        g = session.get(Group, group_id)
        if g is None:
            return None
        member_rows = list(
            session.execute(
                select(Device)
                .join(GroupMembership, GroupMembership.device_id == Device.id)
                .where(GroupMembership.group_id == group_id)
                .order_by(Device.display_name)
            )
        )
        members = [
            {
                "id": d[0].id,
                "display_name": d[0].display_name,
                "registration_state": d[0].registration_state,
                "last_heartbeat_at": d[0].last_heartbeat_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if d[0].last_heartbeat_at
                else None,
            }
            for d in member_rows
        ]
        # v0.2.9: per-record audit slice for groups too.
        from app.services import audit as audit_service

        audit_history = audit_service.query(
            target_type="group", target_id=group_id, limit=25
        )
        return {
            **serialize_group(g, member_count=len(members)),
            "members": members,
            "audit_history": audit_history,
        }


def add_members(group_id: str, device_ids: list[str]) -> int:
    added = 0
    with session_scope() as session:
        g = session.get(Group, group_id)
        if g is None:
            raise LookupError(group_id)
        for did in device_ids:
            d = session.get(Device, did)
            if d is None:
                continue
            existing = session.scalar(
                select(GroupMembership).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.device_id == did,
                )
            )
            if existing is not None:
                continue
            session.add(GroupMembership(group_id=group_id, device_id=did))
            added += 1
        session.flush()
    return added


def remove_member(group_id: str, device_id: str) -> bool:
    with session_scope() as session:
        existing = session.scalar(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.device_id == device_id,
            )
        )
        if existing is None:
            return False
        session.delete(existing)
        session.flush()
        return True


def delete_group(group_id: str) -> bool:
    """Hard-delete a group + all of its memberships (cascade)."""
    with session_scope() as session:
        g = session.get(Group, group_id)
        if g is None:
            return False
        session.delete(g)
        session.flush()
        return True
