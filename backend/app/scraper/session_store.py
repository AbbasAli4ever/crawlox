import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.models import Session


def _domain(url: str) -> str:
    return urlparse(url).netloc


async def load_cookies(db: AsyncSession, user_id: uuid.UUID, url: str) -> list[dict]:
    """Return stored cookies for the domain of url, or [] if none / expired."""
    domain = _domain(url)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(Session).where(
            Session.user_id == user_id,
            Session.domain == domain,
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        return []
    if session.expires_at and session.expires_at < now:
        await db.delete(session)
        await db.commit()
        return []

    return session.cookies if isinstance(session.cookies, list) else []


async def save_cookies(
    db: AsyncSession,
    user_id: uuid.UUID,
    url: str,
    cookies: list[dict],
    user_agent: str | None = None,
    ttl_days: int = 7,
) -> None:
    """Upsert cookies for the domain of url into the sessions table."""
    domain = _domain(url)
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    result = await db.execute(
        select(Session).where(
            Session.user_id == user_id,
            Session.domain == domain,
        )
    )
    session = result.scalar_one_or_none()

    if session:
        session.cookies = cookies
        session.expires_at = expires_at
        if user_agent:
            session.user_agent = user_agent
    else:
        session = Session(
            id=uuid.uuid4(),
            user_id=user_id,
            domain=domain,
            cookies=cookies,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        db.add(session)

    await db.commit()
