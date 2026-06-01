import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import redis.asyncio as aioredis

from app.main import app
from app.db.base import Base, get_db
from app.config import settings

TEST_DATABASE_URL = "postgresql+asyncpg://crawlox:crawlox@postgres:5432/crawlox_test"


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_engine):
    AsyncSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_db():
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(autouse=True)
async def flush_rate_limits():
    """Reset Redis singleton and clear rate limit keys before each test."""
    import app.core.ratelimit as rl_module
    # Force a fresh Redis connection for the current event loop
    if rl_module._redis is not None:
        await rl_module._redis.aclose()
        rl_module._redis = None

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    keys = await r.keys("auth:*")
    if keys:
        await r.delete(*keys)
    await r.aclose()
    yield
    # Reset again after test so next test gets a clean connection
    if rl_module._redis is not None:
        await rl_module._redis.aclose()
        rl_module._redis = None
