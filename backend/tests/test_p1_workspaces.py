from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.models.entities import IdempotencyRecord, User, WorkspaceMember
from app.schemas.auth import RegisterRequest
from app.schemas.workspaces import MemberCreate
from app.services.auth import AuthService
from app.services.workspaces import (
    MemberService,
    canonical_request_hash,
    hash_idempotency_key,
)
from tests.conftest import auth_headers, register


async def _personal_workspace(client, token: str) -> dict:
    response = await client.get("/api/v1/workspaces", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    workspace = body["items"][0]
    assert workspace["kind"] == "PERSONAL"
    assert workspace["role"] == "OWNER"
    assert workspace["member_status"] == "ACTIVE"
    assert workspace["is_default"] is True
    return workspace


async def _team_workspace(client, token: str, key: str = "workspace-key-001") -> dict:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "研发团队"},
        headers={**auth_headers(token), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_registration_provisions_personal_workspace_and_legacy_is_fixed_to_it(client):
    token, _ = await register(client, "workspace-owner@example.com")
    personal = await _personal_workspace(client, token)
    team = await _team_workspace(client, token)

    legacy = await client.post(
        "/api/v1/inspirations",
        json={"title": "只在个人空间", "content": "旧 URL 不跟随 UI 选择"},
        headers=auth_headers(token),
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["workspace_id"] == personal["id"]
    assert legacy.headers["Deprecation"] == "true"
    assert f"/workspaces/{personal['id']}/inspirations" in legacy.headers["Link"]

    team_items = await client.get(
        f"/api/v1/workspaces/{team['id']}/inspirations",
        headers=auth_headers(token),
    )
    assert team_items.status_code == 200
    assert team_items.json()["total"] == 0


async def test_workspace_create_idempotency_replay_and_conflict(client):
    token, _ = await register(client, "workspace-idem@example.com")
    headers = {**auth_headers(token), "Idempotency-Key": "workspace-idem-001"}
    first = await client.post(
        "/api/v1/workspaces", json={"name": "同一空间"}, headers=headers
    )
    renamed = await client.patch(
        f"/api/v1/workspaces/{first.json()['id']}",
        json={"name": "后续改名", "version": 1},
        headers=auth_headers(token),
    )
    assert renamed.status_code == 200
    replay = await client.post(
        "/api/v1/workspaces", json={"name": "同一空间"}, headers=headers
    )
    conflict = await client.post(
        "/api/v1/workspaces", json={"name": "不同空间"}, headers=headers
    )
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    missing_key = await client.post(
        "/api/v1/workspaces",
        json={"name": "缺键"},
        headers=auth_headers(token),
    )
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["code"] == "invalid_idempotency_key"


async def test_rbac_hidden_existence_and_inactive_member(client):
    owner_token, _ = await register(client, "rbac-owner@example.com")
    viewer_token, viewer_user = await register(client, "rbac-viewer@example.com")
    outsider_token, _ = await register(client, "rbac-outsider@example.com")
    team = await _team_workspace(client, owner_token, "workspace-rbac-001")
    add_headers = {
        **auth_headers(owner_token),
        "Idempotency-Key": "member-create-001",
    }
    added = await client.post(
        f"/api/v1/workspaces/{team['id']}/members",
        json={"email": "rbac-viewer@example.com", "role": "VIEWER"},
        headers=add_headers,
    )
    replay = await client.post(
        f"/api/v1/workspaces/{team['id']}/members",
        json={"email": "rbac-viewer@example.com", "role": "VIEWER"},
        headers=add_headers,
    )
    assert added.status_code == replay.status_code == 201
    assert added.json() == replay.json()
    assert added.json()["email"] == "rbac-viewer@example.com"
    replay_conflict = await client.post(
        f"/api/v1/workspaces/{team['id']}/members",
        json={"email": "rbac-viewer@example.com", "role": "EDITOR"},
        headers=add_headers,
    )
    assert replay_conflict.status_code == 409
    assert replay_conflict.json()["error"]["code"] == "idempotency_conflict"

    visible = await client.get(
        f"/api/v1/workspaces/{team['id']}", headers=auth_headers(viewer_token)
    )
    assert visible.status_code == 200
    denied = await client.post(
        f"/api/v1/workspaces/{team['id']}/tags",
        json={"name": "VIEWER 不可写"},
        headers={
            **auth_headers(viewer_token),
            "Idempotency-Key": "viewer-tag-denied",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"
    hidden = await client.get(
        f"/api/v1/workspaces/{team['id']}", headers=auth_headers(outsider_token)
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "resource_not_found"

    inspiration = await client.post(
        f"/api/v1/workspaces/{team['id']}/inspirations",
        json={"content": "属于第一个团队"},
        headers={
            **auth_headers(owner_token),
            "Idempotency-Key": "cross-workspace-inspiration",
        },
    )
    second_team = await _team_workspace(
        client, owner_token, "workspace-rbac-second"
    )
    second_add = await client.post(
        f"/api/v1/workspaces/{second_team['id']}/members",
        json={"email": "rbac-viewer@example.com", "role": "VIEWER"},
        headers={
            **auth_headers(owner_token),
            "Idempotency-Key": "member-create-second",
        },
    )
    assert second_add.status_code == 201
    cross_workspace = await client.get(
        f"/api/v1/workspaces/{second_team['id']}/inspirations/"
        f"{inspiration.json()['id']}",
        headers=auth_headers(viewer_token),
    )
    assert cross_workspace.status_code == 404
    assert cross_workspace.json()["error"]["code"] == "resource_not_found"
    left = await client.delete(
        f"/api/v1/workspaces/{second_team['id']}/members/{viewer_user['id']}",
        params={"version": 1},
        headers={
            **auth_headers(viewer_token),
            "Idempotency-Key": "viewer-self-leave",
        },
    )
    assert left.status_code == 200
    assert left.json()["status"] == "REMOVED"

    suspended = await client.patch(
        f"/api/v1/workspaces/{team['id']}/members/{viewer_user['id']}",
        json={"version": 1, "status": "SUSPENDED"},
        headers={
            **auth_headers(owner_token),
            "Idempotency-Key": "member-suspend-001",
        },
    )
    assert suspended.status_code == 200
    add_replay_after_change = await client.post(
        f"/api/v1/workspaces/{team['id']}/members",
        json={"email": "rbac-viewer@example.com", "role": "VIEWER"},
        headers=add_headers,
    )
    assert add_replay_after_change.status_code == 201
    assert add_replay_after_change.json() == added.json()
    hidden_after_suspend = await client.get(
        f"/api/v1/workspaces/{team['id']}", headers=auth_headers(viewer_token)
    )
    assert hidden_after_suspend.status_code == 404


async def test_personal_owner_and_last_owner_are_protected(client):
    token, user = await register(client, "owner-protection@example.com")
    personal = await _personal_workspace(client, token)
    protected = await client.patch(
        f"/api/v1/workspaces/{personal['id']}/members/{user['id']}",
        json={"version": 1, "role": "ADMIN"},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "personal-owner-001",
        },
    )
    assert protected.status_code == 409
    assert (
        protected.json()["error"]["code"]
        == "personal_workspace_owner_required"
    )

    team = await _team_workspace(client, token, "workspace-last-owner")
    last_owner = await client.delete(
        f"/api/v1/workspaces/{team['id']}/members/{user['id']}",
        params={"version": 1},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "last-owner-delete",
        },
    )
    assert last_owner.status_code == 409
    assert last_owner.json()["error"]["code"] == "last_owner_required"


async def test_admin_editor_and_viewer_can_leave_team_workspace(client):
    owner_token, _ = await register(client, "leave-owner@example.com")
    members = []
    for role in ("ADMIN", "EDITOR", "VIEWER"):
        token, user = await register(client, f"leave-{role.lower()}@example.com")
        members.append((role, token, user))
    team = await _team_workspace(client, owner_token, "workspace-leave-roles")
    for role, _, user in members:
        added = await client.post(
            f"/api/v1/workspaces/{team['id']}/members",
            json={"email": user["email"], "role": role},
            headers={
                **auth_headers(owner_token),
                "Idempotency-Key": f"add-leave-{role.lower()}",
            },
        )
        assert added.status_code == 201, added.text

    for role, token, user in members:
        left = await client.delete(
            f"/api/v1/workspaces/{team['id']}/members/{user['id']}",
            params={"version": 1},
            headers={
                **auth_headers(token),
                "Idempotency-Key": f"self-leave-{role.lower()}",
            },
        )
        assert left.status_code == 200, left.text
        assert left.json()["status"] == "REMOVED"
        hidden = await client.get(
            f"/api/v1/workspaces/{team['id']}",
            headers=auth_headers(token),
        )
        assert hidden.status_code == 404

    owner_still_active = await client.get(
        f"/api/v1/workspaces/{team['id']}",
        headers=auth_headers(owner_token),
    )
    assert owner_still_active.status_code == 200


async def test_inspiration_create_and_patch_replay_exact_first_response(client):
    token, _ = await register(client, "inspiration-idem@example.com")
    team = await _team_workspace(client, token, "workspace-inspiration-idem")
    base = f"/api/v1/workspaces/{team['id']}/inspirations"
    create_headers = {
        **auth_headers(token),
        "Idempotency-Key": "inspiration-create-exact",
    }
    created = await client.post(
        base,
        json={"title": "首次标题", "content": "首次正文"},
        headers=create_headers,
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    first_patch_headers = {
        **auth_headers(token),
        "Idempotency-Key": "inspiration-patch-first",
    }
    first_patch = await client.patch(
        f"{base}/{item_id}",
        json={"version": 1, "title": "第二版标题"},
        headers=first_patch_headers,
    )
    assert first_patch.status_code == 200, first_patch.text
    later_patch = await client.patch(
        f"{base}/{item_id}",
        json={"version": 2, "content": "第三版正文"},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "inspiration-patch-later",
        },
    )
    assert later_patch.status_code == 200, later_patch.text
    assert later_patch.json()["version"] == 3

    create_replay = await client.post(
        base,
        json={"title": "首次标题", "content": "首次正文"},
        headers=create_headers,
    )
    assert create_replay.status_code == 201
    assert create_replay.json() == created.json()

    lost_response_retry = await client.patch(
        f"{base}/{item_id}",
        json={"version": 1, "title": "第二版标题"},
        headers=first_patch_headers,
    )
    assert lost_response_retry.status_code == 200
    assert lost_response_retry.json() == first_patch.json()

    conflict = await client.patch(
        f"{base}/{item_id}",
        json={"version": 1, "title": "复用键但参数不同"},
        headers=first_patch_headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


async def test_inspiration_delete_and_restore_replay_first_response(client):
    token, _ = await register(client, "inspiration-delete-idem@example.com")
    team = await _team_workspace(client, token, "workspace-delete-restore")
    base = f"/api/v1/workspaces/{team['id']}/inspirations"
    created = await client.post(
        base,
        json={"content": "删除与恢复幂等"},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "delete-restore-create",
        },
    )
    item_id = created.json()["id"]
    delete_headers = {
        **auth_headers(token),
        "Idempotency-Key": "inspiration-delete-first",
    }
    deleted = await client.delete(
        f"{base}/{item_id}",
        params={"version": 1},
        headers=delete_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["version"] == 2
    assert deleted.json()["deleted_at"] is not None
    delete_replay = await client.delete(
        f"{base}/{item_id}",
        params={"version": 1},
        headers=delete_headers,
    )
    assert delete_replay.status_code == 200
    assert delete_replay.json() == deleted.json()

    restore_headers = {
        **auth_headers(token),
        "Idempotency-Key": "inspiration-restore-first",
    }
    restored = await client.post(
        f"{base}/{item_id}/restore",
        json={"version": 2},
        headers=restore_headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    assert restored.json()["deleted_at"] is None
    restore_replay = await client.post(
        f"{base}/{item_id}/restore",
        json={"version": 2},
        headers=restore_headers,
    )
    assert restore_replay.status_code == 200
    assert restore_replay.json() == restored.json()


async def test_tag_writes_replay_snapshots_and_204_delete(client):
    token, _ = await register(client, "tag-idem@example.com")
    team = await _team_workspace(client, token, "workspace-tag-idem")
    base = f"/api/v1/workspaces/{team['id']}/tags"
    create_headers = {
        **auth_headers(token),
        "Idempotency-Key": "tag-create-first",
    }
    created = await client.post(
        base,
        json={"name": "原始标签", "color": "#112233"},
        headers=create_headers,
    )
    assert created.status_code == 201, created.text
    tag_id = created.json()["id"]
    first_patch_headers = {
        **auth_headers(token),
        "Idempotency-Key": "tag-patch-first",
    }
    first_patch = await client.patch(
        f"{base}/{tag_id}",
        json={"version": 1, "name": "第二版标签"},
        headers=first_patch_headers,
    )
    assert first_patch.status_code == 200, first_patch.text
    later_patch = await client.patch(
        f"{base}/{tag_id}",
        json={"version": 2, "color": "#445566"},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "tag-patch-later",
        },
    )
    assert later_patch.status_code == 200
    assert later_patch.json()["version"] == 3

    create_replay = await client.post(
        base,
        json={"name": "原始标签", "color": "#112233"},
        headers=create_headers,
    )
    assert create_replay.json() == created.json()
    patch_replay = await client.patch(
        f"{base}/{tag_id}",
        json={"version": 1, "name": "第二版标签"},
        headers=first_patch_headers,
    )
    assert patch_replay.json() == first_patch.json()

    delete_headers = {
        **auth_headers(token),
        "Idempotency-Key": "tag-delete-first",
    }
    deleted = await client.delete(
        f"{base}/{tag_id}",
        params={"version": 3},
        headers=delete_headers,
    )
    assert deleted.status_code == 204
    delete_replay = await client.delete(
        f"{base}/{tag_id}",
        params={"version": 3},
        headers=delete_headers,
    )
    assert delete_replay.status_code == 204
    create_after_delete = await client.post(
        base,
        json={"name": "原始标签", "color": "#112233"},
        headers=create_headers,
    )
    assert create_after_delete.status_code == 201
    assert create_after_delete.json() == created.json()
    patch_after_delete = await client.patch(
        f"{base}/{tag_id}",
        json={"version": 1, "name": "第二版标签"},
        headers=first_patch_headers,
    )
    assert patch_after_delete.status_code == 200
    assert patch_after_delete.json() == first_patch.json()
    create_conflict = await client.post(
        base,
        json={"name": "不同标签", "color": "#112233"},
        headers=create_headers,
    )
    assert create_conflict.status_code == 409
    assert create_conflict.json()["error"]["code"] == "idempotency_conflict"
    patch_conflict = await client.patch(
        f"{base}/{tag_id}",
        json={"version": 1, "name": "不同标签"},
        headers=first_patch_headers,
    )
    assert patch_conflict.status_code == 409
    assert patch_conflict.json()["error"]["code"] == "idempotency_conflict"
    conflict = await client.delete(
        f"{base}/{tag_id}",
        params={"version": 2},
        headers=delete_headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


async def test_untrusted_request_id_and_tokens_never_enter_audit(client):
    token, _ = await register(client, "audit-header-owner@example.com")
    malicious = "victim@example.com-Bearer-secret-token"
    created = await client.post(
        "/api/v1/workspaces",
        json={"name": "安全审计空间"},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "safe-request-id-test",
            "X-Request-ID": malicious,
        },
    )
    assert created.status_code == 201, created.text
    generated_request_id = created.headers["X-Request-ID"]
    UUID(generated_request_id)
    assert generated_request_id != malicious

    audit = await client.get(
        f"/api/v1/workspaces/{created.json()['id']}/audit-logs",
        headers=auth_headers(token),
    )
    assert audit.status_code == 200
    assert malicious not in audit.text
    assert token not in audit.text
    assert "audit-header-owner@example.com" not in audit.text
    assert any(
        item["request_id"] == generated_request_id
        for item in audit.json()["items"]
    )


async def test_admin_cannot_manage_admin_and_audit_is_redacted(client):
    owner_token, _ = await register(client, "audit-owner@example.com")
    admin_token, admin_user = await register(client, "audit-admin@example.com")
    target_token, target_user = await register(client, "audit-target@example.com")
    team = await _team_workspace(client, owner_token, "workspace-audit-001")
    owner_headers = auth_headers(owner_token)
    for email, role, key in (
        ("audit-admin@example.com", "ADMIN", "member-admin-001"),
        ("audit-target@example.com", "EDITOR", "member-editor-001"),
    ):
        response = await client.post(
            f"/api/v1/workspaces/{team['id']}/members",
            json={"email": email, "role": role},
            headers={**owner_headers, "Idempotency-Key": key},
        )
        assert response.status_code == 201, response.text

    denied = await client.patch(
        f"/api/v1/workspaces/{team['id']}/members/{admin_user['id']}",
        json={"version": 1, "role": "VIEWER"},
        headers={
            **auth_headers(admin_token),
            "Idempotency-Key": "admin-self-change",
        },
    )
    assert denied.status_code == 403

    inspiration = await client.post(
        f"/api/v1/workspaces/{team['id']}/inspirations",
        json={
            "title": "绝密标题",
            "content": "绝密正文",
            "source_url": "https://secret.example/path",
        },
        headers={
            **auth_headers(target_token),
            "Idempotency-Key": "inspiration-create-audit",
        },
    )
    assert inspiration.status_code == 201, inspiration.text
    replay = await client.post(
        f"/api/v1/workspaces/{team['id']}/inspirations",
        json={
            "title": "绝密标题",
            "content": "绝密正文",
            "source_url": "https://secret.example/path",
        },
        headers={
            **auth_headers(target_token),
            "Idempotency-Key": "inspiration-create-audit",
        },
    )
    assert replay.status_code == 201
    assert replay.json() == inspiration.json()

    audit = await client.get(
        f"/api/v1/workspaces/{team['id']}/audit-logs",
        params={"limit": 100},
        headers=owner_headers,
    )
    assert audit.status_code == 200, audit.text
    body = audit.json()
    assert "next_cursor" in body
    serialized = audit.text
    for secret in (
        "audit-owner@example.com",
        "audit-admin@example.com",
        "audit-target@example.com",
        "绝密标题",
        "绝密正文",
        "secret.example",
        "workspace-audit-001",
        "inspiration-create-audit",
    ):
        assert secret not in serialized
    assert any(item["action"] == "inspiration.created" for item in body["items"])
    assert target_user["id"] in serialized


async def test_unknown_member_candidate_and_authentication_error_contract(client):
    token, _ = await register(client, "candidate-owner@example.com")
    team = await _team_workspace(client, token, "workspace-candidate")
    missing = await client.post(
        f"/api/v1/workspaces/{team['id']}/members",
        json={"email": "missing@example.com", "role": "VIEWER"},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "missing-candidate",
        },
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "member_candidate_not_found"

    unauthenticated = await client.get("/api/v1/workspaces")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "authentication_required"


async def test_registration_rolls_back_user_when_personal_workspace_creation_fails(
    session_factory, monkeypatch
):
    async def fail_provision(*args, **kwargs):
        raise RuntimeError("forced workspace failure")

    monkeypatch.setattr("app.services.auth.provision_personal_workspace", fail_provision)
    settings = Settings(
        _env_file=None,
        app_env="test",
        jwt_secret="test-secret-that-is-longer-than-32-characters",
    )
    async with session_factory() as session:
        with pytest.raises(RuntimeError, match="forced workspace failure"):
            await AuthService(session, settings).register(
                RegisterRequest(
                    email="rollback@example.com",
                    password="StrongPass123!",
                    display_name="回滚用户",
                )
            )
        assert (
            await session.scalar(
                select(func.count()).select_from(User).where(
                    User.email == "rollback@example.com"
                )
            )
            == 0
        )


async def test_member_create_recovers_same_key_winner_after_concurrent_flush_conflict():
    workspace_id = uuid4()
    actor_id = uuid4()
    candidate = User(
        id=uuid4(),
        email="race-candidate@example.com",
        password_hash="unused",
        display_name="并发候选人",
    )
    payload = MemberCreate(email=candidate.email, role="VIEWER")
    key = "member-race-same-key"
    request_hash = canonical_request_hash(
        {"email": candidate.email, "role": payload.role}
    )
    now = datetime.now(UTC)
    winner_member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=candidate.id,
        role="VIEWER",
        status="ACTIVE",
        version=1,
        invited_by_user_id=actor_id,
        created_at=now,
        updated_at=now,
    )
    replay_json = {
        "workspace_id": str(workspace_id),
        "user_id": str(candidate.id),
        "email": candidate.email,
        "display_name": candidate.display_name,
        "role": "VIEWER",
        "status": "ACTIVE",
        "version": 1,
        "joined_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    winner = IdempotencyRecord(
        workspace_id=workspace_id,
        actor_id=actor_id,
        scope="membership.create",
        key_hash=hash_idempotency_key(key),
        request_hash=request_hash,
        response_status=201,
        response_json=replay_json,
        resource_type="workspace_member",
        resource_id=candidate.id,
        expires_at=now + timedelta(days=1),
    )

    class RaceSession:
        async def flush(self):
            raise IntegrityError("INSERT", {}, Exception("duplicate"))

        async def rollback(self):
            return None

    class Access:
        async def require(self, *args, **kwargs):
            return SimpleNamespace(id=workspace_id), SimpleNamespace(role="OWNER")

    class Idempotency:
        calls = 0

        async def get(self, *args):
            self.calls += 1
            return None if self.calls == 1 else winner

        def add(self, record):
            raise AssertionError("flush conflict occurs before loser adds record")

    class Members:
        calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            return None if self.calls == 1 else winner_member

        def add(self, member):
            return None

    class Users:
        async def get_active_by_email(self, email):
            return candidate

        async def get_by_id(self, user_id):
            return candidate

    service = MemberService(RaceSession(), actor_id)  # type: ignore[arg-type]
    service.access = Access()  # type: ignore[assignment]
    service.idempotency = Idempotency()  # type: ignore[assignment]
    service.members = Members()  # type: ignore[assignment]
    service.users = Users()  # type: ignore[assignment]
    member, user, response_json = await service.add(
        workspace_id,
        payload,
        key,
        "00000000-0000-0000-0000-000000000001",
    )
    assert member is winner_member
    assert user is candidate
    assert response_json == replay_json
