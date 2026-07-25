from __future__ import annotations

from tests.conftest import auth_headers, register


async def test_health_register_login_and_me(client):
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    assert (await client.get("/health/ready")).status_code == 200

    token, user = await register(client)
    duplicate = await client.post(
        "/api/v1/auth/register",
        json={"email": "USER@example.com", "password": "StrongPass123!", "display_name": "重复"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "email_exists"

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "StrongPass123!"},
    )
    assert login.status_code == 200
    me = await client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]
    assert "X-Request-ID" in me.headers


async def test_inspiration_crud_search_soft_delete_restore_and_user_isolation(client):
    owner_token, _ = await register(client, "owner@example.com")
    other_token, _ = await register(client, "other@example.com")
    owner = auth_headers(owner_token)

    tag_response = await client.post(
        "/api/v1/tags", json={"name": "产品想法", "color": "#3366FF"}, headers=owner
    )
    assert tag_response.status_code == 201
    tag = tag_response.json()

    create = await client.post(
        "/api/v1/inspirations",
        json={
            "title": "晨间记录",
            "content": "做一个能快速记录灵感的工具",
            "tag_ids": [tag["id"]],
        },
        headers=owner,
    )
    assert create.status_code == 201, create.text
    item = create.json()
    assert item["version"] == 1

    forbidden_as_not_found = await client.get(
        f"/api/v1/inspirations/{item['id']}", headers=auth_headers(other_token)
    )
    assert forbidden_as_not_found.status_code == 404

    found = await client.get(
        "/api/v1/inspirations",
        params=[("q", "快速"), ("tag_ids", tag["id"]), ("tag_match", "any")],
        headers=owner,
    )
    assert found.status_code == 200
    assert found.json()["total"] == 1

    updated = await client.patch(
        f"/api/v1/inspirations/{item['id']}",
        json={"version": 1, "title": "更新后的标题"},
        headers=owner,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2

    stale = await client.patch(
        f"/api/v1/inspirations/{item['id']}",
        json={"version": 1, "title": "冲突写入"},
        headers=owner,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    stale_delete = await client.delete(
        f"/api/v1/inspirations/{item['id']}", params={"version": 1}, headers=owner
    )
    assert stale_delete.status_code == 409

    deleted = await client.delete(
        f"/api/v1/inspirations/{item['id']}", params={"version": 2}, headers=owner
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_at"] is not None
    missing = await client.get(f"/api/v1/inspirations/{item['id']}", headers=owner)
    assert missing.status_code == 404

    restored = await client.post(
        f"/api/v1/inspirations/{item['id']}/restore",
        json={"version": 3},
        headers=owner,
    )
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None


async def test_tag_optimistic_lock_and_ownership(client):
    token, _ = await register(client, "tags@example.com")
    other_token, _ = await register(client, "tags-other@example.com")
    headers = auth_headers(token)
    created = await client.post("/api/v1/tags", json={"name": "阅读"}, headers=headers)
    tag = created.json()

    hidden = await client.patch(
        f"/api/v1/tags/{tag['id']}",
        json={"version": 1, "name": "越权"},
        headers=auth_headers(other_token),
    )
    assert hidden.status_code == 404

    updated = await client.patch(
        f"/api/v1/tags/{tag['id']}",
        json={"version": 1, "name": "待读"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    stale = await client.patch(
        f"/api/v1/tags/{tag['id']}",
        json={"version": 1, "color": "#112233"},
        headers=headers,
    )
    assert stale.status_code == 409

    inspiration = await client.post(
        "/api/v1/inspirations",
        json={"content": "使用标签", "tag_ids": [tag["id"]]},
        headers=headers,
    )
    assert inspiration.status_code == 201
    in_use = await client.delete(
        f"/api/v1/tags/{tag['id']}",
        params={"version": 2},
        headers=headers,
    )
    assert in_use.status_code == 409
    assert in_use.json()["error"]["code"] == "tag_in_use"
    forced = await client.delete(
        f"/api/v1/tags/{tag['id']}",
        params={"version": 2, "force": "true"},
        headers=headers,
    )
    assert forced.status_code == 204


async def test_tag_usage_count_contract_and_soft_delete_semantics(client):
    token, _ = await register(client, "tag-count@example.com")
    other_token, _ = await register(client, "tag-count-other@example.com")
    headers = auth_headers(token)
    other_headers = auth_headers(other_token)

    created = await client.post(
        "/api/v1/tags", json={"name": "美食"}, headers=headers
    )
    assert created.status_code == 201, created.text
    tag = created.json()
    assert tag["usage_count"] == 0

    other_tag = (
        await client.post(
            "/api/v1/tags", json={"name": "其他用户标签"}, headers=other_headers
        )
    ).json()
    await client.post(
        "/api/v1/inspirations",
        json={"content": "其他用户的灵感", "tag_ids": [other_tag["id"]]},
        headers=other_headers,
    )

    inspiration_response = await client.post(
        "/api/v1/inspirations",
        json={"content": "辣椒包肉做法", "tag_ids": [tag["id"]]},
        headers=headers,
    )
    assert inspiration_response.status_code == 201, inspiration_response.text
    inspiration = inspiration_response.json()
    assert "usage_count" not in inspiration["tags"][0]

    listed = await client.get("/api/v1/tags", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [(item["id"], item["usage_count"]) for item in listed.json()] == [
        (tag["id"], 1)
    ]

    deleted = await client.delete(
        f"/api/v1/inspirations/{inspiration['id']}",
        params={"version": inspiration["version"]},
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text

    after_soft_delete = await client.get("/api/v1/tags", headers=headers)
    assert after_soft_delete.json()[0]["usage_count"] == 1

    updated = await client.patch(
        f"/api/v1/tags/{tag['id']}",
        json={"version": tag["version"], "name": "家常美食"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["usage_count"] == 1


async def test_ai_fallback_and_idempotency(client):
    token, _ = await register(client, "ai@example.com")
    headers = auth_headers(token)
    created = await client.post(
        "/api/v1/inspirations",
        json={"title": "通勤", "content": "在通勤时用语音记录稍纵即逝的想法"},
        headers=headers,
    )
    item_id = created.json()["id"]
    ai_headers = {**headers, "Idempotency-Key": "fixed-key-001"}
    first = await client.post(
        f"/api/v1/inspirations/{item_id}/ai-generations",
        json={"operation": "summary"},
        headers=ai_headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["mode"] == "fallback"
    assert first.json()["result"].startswith("摘要：")
    assert first.json()["warning"]
    assert set(first.json()) == {
        "generation_id",
        "operation",
        "mode",
        "result",
        "warning",
        "created_at",
    }

    unchanged = await client.get(f"/api/v1/inspirations/{item_id}", headers=headers)
    assert unchanged.status_code == 200
    assert unchanged.json()["version"] == 1
    assert unchanged.json()["summary"] is None
    assert unchanged.json()["expanded_content"] is None

    replay = await client.post(
        f"/api/v1/inspirations/{item_id}/ai-generations",
        json={"operation": "summary"},
        headers=ai_headers,
    )
    assert replay.status_code == 200
    assert replay.json()["generation_id"] == first.json()["generation_id"]
    assert replay.json() == first.json()

    conflict = await client.post(
        f"/api/v1/inspirations/{item_id}/ai-generations",
        json={"operation": "expand"},
        headers=ai_headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    unknown_field = await client.patch(
        f"/api/v1/inspirations/{item_id}",
        json={"version": 1, "ai_result": first.json()["result"]},
        headers=headers,
    )
    assert unknown_field.status_code == 422

    save_summary = await client.patch(
        f"/api/v1/inspirations/{item_id}",
        json={"version": 1, "summary": first.json()["result"]},
        headers=headers,
    )
    assert save_summary.status_code == 200, save_summary.text
    assert save_summary.json()["summary"] == first.json()["result"]
    assert save_summary.json()["version"] == 2

    expand_preview = await client.post(
        f"/api/v1/inspirations/{item_id}/ai-generations",
        json={"operation": "expand", "instruction": "整理为行动方案"},
        headers={**headers, "Idempotency-Key": "fixed-key-002"},
    )
    assert expand_preview.status_code == 200, expand_preview.text
    assert expand_preview.json()["operation"] == "expand"
    before_expand_save = await client.get(
        f"/api/v1/inspirations/{item_id}", headers=headers
    )
    assert before_expand_save.json()["version"] == 2
    assert before_expand_save.json()["expanded_content"] is None

    save_expand = await client.patch(
        f"/api/v1/inspirations/{item_id}",
        json={"version": 2, "expanded_content": expand_preview.json()["result"]},
        headers=headers,
    )
    assert save_expand.status_code == 200, save_expand.text
    assert save_expand.json()["expanded_content"] == expand_preview.json()["result"]
    assert save_expand.json()["version"] == 3

    persisted = await client.get(f"/api/v1/inspirations/{item_id}", headers=headers)
    assert persisted.json()["summary"] == first.json()["result"]
    assert persisted.json()["expanded_content"] == expand_preview.json()["result"]
    assert persisted.json()["version"] == 3

    cleared = await client.patch(
        f"/api/v1/inspirations/{item_id}",
        json={"version": 3, "summary": None, "expanded_content": None},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["summary"] is None
    assert cleared.json()["expanded_content"] is None
    assert cleared.json()["version"] == 4


async def test_validation_error_uses_unified_shape(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-email", "password": "short", "display_name": ""},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["request_id"]


async def test_http_exception_uses_unified_shape(client):
    response = await client.get("/route-that-does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "http_error"
    assert body["request_id"]


async def test_login_failure_rate_limit_by_ip_and_account(client):
    await register(client, "limited@example.com")
    for _ in range(4):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com", "password": "wrong-password"},
        )
    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "limited@example.com", "password": "wrong-password"},
    )
    assert response.status_code in {401, 429}
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "login_rate_limited"


async def test_inspiration_combined_filters_and_sort(client):
    token, _ = await register(client, "filters@example.com")
    headers = auth_headers(token)
    tag_a = (
        await client.post("/api/v1/tags", json={"name": "A"}, headers=headers)
    ).json()
    tag_b = (
        await client.post("/api/v1/tags", json={"name": "B"}, headers=headers)
    ).json()
    first = (
        await client.post(
            "/api/v1/inspirations",
            json={
                "title": "目标灵感",
                "content": "联合筛选",
                "tag_ids": [tag_a["id"], tag_b["id"]],
            },
            headers=headers,
        )
    ).json()
    await client.post(
        "/api/v1/inspirations",
        json={"title": "其他灵感", "content": "联合筛选", "tag_ids": [tag_a["id"]]},
        headers=headers,
    )

    response = await client.get(
        "/api/v1/inspirations",
        params=[
            ("q", "联合"),
            ("tag_ids", tag_a["id"]),
            ("tag_ids", tag_b["id"]),
            ("tag_match", "all"),
            ("date_from", "2000-01-01T00:00:00Z"),
            ("date_to", "2100-01-01T00:00:00Z"),
            ("sort", "created_asc"),
            ("page", "1"),
            ("page_size", "10"),
        ],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == first["id"]
