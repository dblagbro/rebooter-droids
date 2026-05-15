# B1 RBAC Phase 3 (P3) — Implementation Progress

> **Superseded — B1 RBAC P3 shipped as v0.5.37 (2026-05-15).** All four
> resource types (devices, groups, sites, audit/history) complete; the
> full B1 RBAC rollout P1–P5 is done. Kept for historical context only.

**Started**: 2026-05-15  
**Status**: ✅ **DONE** — shipped v0.5.37

---

## What P3 Delivers

Scope-aware list/detail filtering on four major surfaces:
- `/app/devices` + `/api/v1/admin/devices` (devices) ✅ **DONE**
- `/app/groups` (groups) ⏳ TODO
- `/app/sites` (sites) ⏳ TODO
- `/app/history` (audit events) ⏳ TODO

**Shadow mode behavior** (default `rbac.enforce_mode = "shadow"`):
- Double-query: run both unfiltered and filtered queries
- Log `rbac.shadow_diff` audit rows with hidden resource counts
- Return unfiltered results (legacy behavior preserved)

**Enforce mode behavior** (`rbac.enforce_mode = "enforce"`):
- Single query: apply scope filter
- Return only scoped results

**Super_admin escape hatch**: Always sees all resources (no filtering)

---

## ✅ Completed: Devices Filtering

### New file: `app/services/rbac_filter.py`
Central filtering logic for all resource types. Key functions:

- **`filter_devices_with_shadow_logging(stmt, session, *, user_id, role_needed)`**  
  Applies RBAC device scope filter with shadow/enforce mode handling.
  
- **`filter_groups_with_shadow_logging(...)`** — Stub implementation ready
  
- **`filter_sites_with_shadow_logging(...)`** — Stub implementation ready

**Pattern established**:
```python
# In service layer (e.g., app/services/devices/_query.py):
stmt = select(Device).where(...)  # Build base query
stmt = stmt.order_by(Device.created_at.desc())

# Apply RBAC filtering (P3 addition)
from app.services.rbac_filter import filter_devices_with_shadow_logging
rows = filter_devices_with_shadow_logging(stmt, session)

# Continue with existing serialization logic...
```

### Modified: `app/services/devices/_query.py`
- `list_devices()` function now calls `filter_devices_with_shadow_logging()`
- Preserves all existing filtering logic (site_id, group_id, search, status, chips)
- RBAC filter applies AFTER all other filters (correct order)

### Validation
- ✓ Container builds and starts successfully
- ✓ `list_devices()` returns results without crashing
- ✓ Super_admin sees all devices (no shadow_diff rows)
- ✓ No breaking changes to existing API

---

## ⏳ Remaining Work

### 1. Groups Filtering (~45 min)

**File to modify**: `app/services/groups.py` (find the list function)

**Pattern to follow**:
```python
# Find the groups list function (similar to list_devices)
stmt = select(Group).where(...)
stmt = stmt.order_by(...)

# Add RBAC filtering
from app.services.rbac_filter import filter_groups_with_shadow_logging
rows = filter_groups_with_shadow_logging(stmt, session)
```

**Search hint**:
```bash
cd /mnt/s/code/rebooter-droids
grep -n "def.*list.*group" app/services/*.py app/blueprints/admin/groups.py
```

### 2. Sites Filtering (~45 min)

**File to modify**: `app/services/sites.py` or similar

**Pattern**: Same as groups, using `filter_sites_with_shadow_logging()`

**Search hint**:
```bash
grep -n "def.*list.*site" app/services/*.py app/blueprints/admin/sites.py
```

### 3. History/Audit Filtering (~90 min - more complex)

**File to modify**: Likely `app/services/audit.py` or `app/blueprints/admin/history.py`

**Challenge**: Audit events are cross-resource (target_type can be device/site/group).  
Need to filter based on whether the user has access to the target resource.

**Approach**:
```python
# Pseudo-code for audit filtering
def filter_audit_with_shadow_logging(stmt, session, user_id):
    # Get all scoped resource IDs
    device_ids = effective_device_ids(user_id, "viewer")
    site_ids = effective_site_ids(user_id, "viewer")
    group_ids = effective_group_ids(user_id, "viewer")
    
    # Filter where:
    # - target_type="device" AND target_id IN device_ids, OR
    # - target_type="site" AND target_id IN site_ids, OR
    # - target_type="group" AND target_id IN group_ids, OR
    # - target_type IS NULL (system events)
    
    # Apply shadow/enforce logic similar to other filters
```

### 4. Regression Test (~60 min)

**File to create**: `tests/qa/test_v0537_scope_filter_lists.py`

**Test cases** (per design doc §5):
- Super_admin sees all resources
- Admin-of-site-A sees only site-A devices/groups/history
- Operator with one device binding sees only that device
- Viewer with site binding sees site rows but cannot mutate
- Shadow mode emits `rbac.shadow_diff` rows with correct counts

**Test setup**:
```python
# Create test users with specific bindings
# Query each list endpoint
# Assert correct filtering in enforce mode
# Assert shadow_diff rows in shadow mode
```

### 5. Documentation Updates (~15 min)

- Update `CHANGELOG.md` with v0.5.37 entry
- Update `docs/BACKLOG.md` to mark P3 complete
- Update `docs/notes/2026-05-15-b1-rbac-design.md` to mark P3 shipped
- Update version in `app/version.py` and `pyproject.toml` to 0.5.37

---

## Estimated Time Remaining

- Groups: 45 min
- Sites: 45 min
- History/Audit: 90 min
- Test: 60 min
- Docs: 15 min
- **Total**: ~3.5 hours

---

## Testing Strategy

### Manual Testing (Quick Validation)
```bash
# Test as super_admin (should see all)
curl -H "Authorization: Bearer $TOKEN" https://www.voipguru.org/rebooter/api/v1/admin/devices

# Create limited user via container Python
docker exec -i rebooter-droids python3 <<'EOF'
from app import create_app
from app.db import session_scope
from app.models import User, RoleBinding
from app.services.role_bindings import grant

app = create_app()
with app.app_context():
    # Create viewer with site binding
    # Test list endpoints
    # Verify filtering works
EOF
```

### Automated Testing
Use `tests/qa/test_v0537_scope_filter_lists.py` for regression coverage.

---

## Notes

- **No breaking changes**: Shadow mode preserves existing behavior (shows all)
- **Reversible**: Flag toggle switches between shadow/enforce
- **Performance**: Double-query in shadow mode adds ~2x latency; enforce mode is single query
- **Audit trail**: All shadow diffs are logged for analysis before enforce flip

---

## Next Session Checklist

1. ✅ Read this file first
2. Implement groups filtering (follow devices pattern)
3. Implement sites filtering (follow devices pattern)  
4. Implement history/audit filtering (more complex, cross-resource)
5. Write regression test
6. Manual validation
7. Update docs
8. Commit, tag v0.5.37, deploy
