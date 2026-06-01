from datetime import datetime, timedelta, timezone
from typing import Literal

from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str) -> str:
    expire = _now() + timedelta(minutes=settings.jwt_access_ttl_minutes)
    return jwt.encode(
        {"sub": user_id, "type": "access", "exp": expire},
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )


def create_refresh_token(user_id: str) -> str:
    expire = _now() + timedelta(days=settings.jwt_refresh_ttl_days)
    return jwt.encode(
        {"sub": user_id, "type": "refresh", "exp": expire},
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )


def decode_token(token: str, expected_type: Literal["access", "refresh"]) -> str:
    """Decode and validate a JWT. Returns the user_id (sub) on success."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        raise ValueError("Invalid or expired token")

    if payload.get("type") != expected_type:
        raise ValueError(f"Expected {expected_type} token")

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise ValueError("Token missing subject")

    return user_id
