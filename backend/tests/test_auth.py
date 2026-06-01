import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio


REGISTER_URL = "/api/auth/register"
LOGIN_URL = "/api/auth/login"
REFRESH_URL = "/api/auth/refresh"
LOGOUT_URL = "/api/auth/logout"
ME_URL = "/api/auth/me"
VERIFY_URL = "/api/auth/verify-email"


async def register_and_login(client: AsyncClient, email: str, password: str) -> str:
    """Helper: register a user and return an access token."""
    await client.post(REGISTER_URL, json={"email": email, "password": password})
    resp = await client.post(LOGIN_URL, json={"email": email, "password": password})
    return resp.json()["access_token"]


class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        resp = await client.post(REGISTER_URL, json={"email": "new@test.com", "password": "password123"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new@test.com"
        assert data["subscription_tier"] == "free"
        assert data["is_verified"] is False

    async def test_register_duplicate_email(self, client: AsyncClient):
        payload = {"email": "dup@test.com", "password": "password123"}
        await client.post(REGISTER_URL, json=payload)
        resp = await client.post(REGISTER_URL, json=payload)
        assert resp.status_code == 400

    async def test_register_short_password(self, client: AsyncClient):
        resp = await client.post(REGISTER_URL, json={"email": "short@test.com", "password": "abc"})
        assert resp.status_code == 400

    async def test_register_invalid_email(self, client: AsyncClient):
        resp = await client.post(REGISTER_URL, json={"email": "not-an-email", "password": "password123"})
        assert resp.status_code == 422


class TestLogin:
    async def test_login_success(self, client: AsyncClient):
        await client.post(REGISTER_URL, json={"email": "login@test.com", "password": "password123"})
        resp = await client.post(LOGIN_URL, json={"email": "login@test.com", "password": "password123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()
        assert resp.cookies.get("refresh_token") is not None

    async def test_login_wrong_password(self, client: AsyncClient):
        await client.post(REGISTER_URL, json={"email": "wrongpw@test.com", "password": "password123"})
        resp = await client.post(LOGIN_URL, json={"email": "wrongpw@test.com", "password": "wrong"})
        assert resp.status_code == 401

    async def test_login_unknown_email(self, client: AsyncClient):
        resp = await client.post(LOGIN_URL, json={"email": "ghost@test.com", "password": "password123"})
        assert resp.status_code == 401

    async def test_login_rate_limit(self, client: AsyncClient):
        for _ in range(5):
            await client.post(LOGIN_URL, json={"email": "rl@test.com", "password": "wrong"})
        resp = await client.post(LOGIN_URL, json={"email": "rl@test.com", "password": "wrong"})
        assert resp.status_code == 429


class TestMe:
    async def test_me_authenticated(self, client: AsyncClient):
        token = await register_and_login(client, "me@test.com", "password123")
        resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "me@test.com"

    async def test_me_no_token(self, client: AsyncClient):
        resp = await client.get(ME_URL)
        assert resp.status_code == 403 or resp.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        resp = await client.get(ME_URL, headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401


class TestRefresh:
    async def test_refresh_success(self, client: AsyncClient):
        await client.post(REGISTER_URL, json={"email": "refresh@test.com", "password": "password123"})
        login_resp = await client.post(LOGIN_URL, json={"email": "refresh@test.com", "password": "password123"})
        assert login_resp.cookies.get("refresh_token") is not None

        resp = await client.post(REFRESH_URL)
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_refresh_no_cookie(self, client: AsyncClient):
        resp = await client.post(REFRESH_URL)
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_clears_cookie(self, client: AsyncClient):
        await client.post(REGISTER_URL, json={"email": "logout@test.com", "password": "password123"})
        await client.post(LOGIN_URL, json={"email": "logout@test.com", "password": "password123"})

        resp = await client.post(LOGOUT_URL)
        assert resp.status_code == 200
        # After logout, refresh should fail
        refresh_resp = await client.post(REFRESH_URL)
        assert refresh_resp.status_code == 401


class TestEmailVerification:
    async def test_verify_email(self, client: AsyncClient):
        import redis.asyncio as aioredis
        from app.config import settings
        resp = await client.post(REGISTER_URL, json={"email": "verify@test.com", "password": "password123"})
        user_id = resp.json()["id"]

        # Find the token in Redis using a fresh connection
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        keys = await r.keys("verify:*")
        token = None
        for key in keys:
            val = await r.get(key)
            if val == user_id:
                token = key.split("verify:")[1]
                break

        await r.aclose()
        assert token is not None
        verify_resp = await client.get(f"{VERIFY_URL}/{token}")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["message"] == "Email verified successfully"

    async def test_verify_invalid_token(self, client: AsyncClient):
        resp = await client.get(f"{VERIFY_URL}/invalid-token-xyz")
        assert resp.status_code == 400
