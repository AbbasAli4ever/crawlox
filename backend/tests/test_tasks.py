import pytest
from httpx import AsyncClient
from unittest.mock import patch

pytestmark = pytest.mark.asyncio

REGISTER_URL = "/api/auth/register"
LOGIN_URL = "/api/auth/login"
NOOP_URL = "/api/tasks/noop"
TASK_URL = "/api/tasks"


async def get_token(client: AsyncClient) -> str:
    await client.post(REGISTER_URL, json={"email": "taskuser@test.com", "password": "password123"})
    resp = await client.post(LOGIN_URL, json={"email": "taskuser@test.com", "password": "password123"})
    return resp.json()["access_token"]


class TestNoopTask:
    async def test_noop_requires_auth(self, client: AsyncClient):
        resp = await client.post(NOOP_URL)
        assert resp.status_code in (401, 403)

    async def test_noop_creates_task_row(self, client: AsyncClient):
        token = await get_token(client)

        # Patch Celery so it doesn't actually enqueue (no worker in test)
        with patch("app.api.tasks_router.noop_task") as mock_task:
            mock_task.delay.return_value = None
            resp = await client.post(NOOP_URL, headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 202
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "pending"

    async def test_get_task_status(self, client: AsyncClient):
        token = await get_token(client)

        with patch("app.api.tasks_router.noop_task") as mock_task:
            mock_task.delay.return_value = None
            create_resp = await client.post(NOOP_URL, headers={"Authorization": f"Bearer {token}"})

        task_id = create_resp.json()["task_id"]
        resp = await client.get(f"{TASK_URL}/{task_id}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id

    async def test_get_task_not_found(self, client: AsyncClient):
        token = await get_token(client)
        resp = await client.get(
            f"{TASK_URL}/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_task_isolation_between_users(self, client: AsyncClient):
        """User A cannot see User B's task."""
        await client.post(REGISTER_URL, json={"email": "user_a@test.com", "password": "password123"})
        await client.post(REGISTER_URL, json={"email": "user_b@test.com", "password": "password123"})

        token_a_resp = await client.post(LOGIN_URL, json={"email": "user_a@test.com", "password": "password123"})
        token_b_resp = await client.post(LOGIN_URL, json={"email": "user_b@test.com", "password": "password123"})
        token_a = token_a_resp.json()["access_token"]
        token_b = token_b_resp.json()["access_token"]

        with patch("app.api.tasks_router.noop_task") as mock_task:
            mock_task.delay.return_value = None
            task_resp = await client.post(NOOP_URL, headers={"Authorization": f"Bearer {token_a}"})

        task_id = task_resp.json()["task_id"]

        # User B tries to access User A's task — should 404
        resp = await client.get(f"{TASK_URL}/{task_id}", headers={"Authorization": f"Bearer {token_b}"})
        assert resp.status_code == 404
