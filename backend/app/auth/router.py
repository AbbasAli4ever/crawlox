import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.dependencies import get_current_user
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.auth.passwords import hash_password, verify_password
from app.core.ratelimit import is_rate_limited
from app.db.base import get_db
from app.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
COOKIE_OPTS = {
    "httponly": True,
    "secure": False,  # set True in production behind HTTPS
    "samesite": "lax",
    "max_age": 60 * 60 * 24 * 7,  # 7 days
}


# ---------- schemas ----------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    subscription_tier: str
    is_verified: bool
    created_at: datetime


# ---------- endpoints ----------

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(request: Request, body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if await is_rate_limited(f"auth:register:{ip}", max_attempts=5, window_seconds=900):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Try again in 15 minutes.")

    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    if len(body.password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")

    user = User(
        id=uuid.uuid4(),
        email=body.email,
        password_hash=hash_password(body.password),
        # stub: log token to console until email service is wired (Day 4)
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Store verification token in Redis (24h TTL). In production, email this link.
    from app.core.ratelimit import get_redis
    verification_token = secrets.token_urlsafe(32)
    r = get_redis()
    await r.set(f"verify:{verification_token}", str(user.id), ex=86400)
    print(f"[DEV] Verify email: GET /api/auth/verify-email/{verification_token}")

    return UserResponse(
        id=str(user.id),
        email=user.email,
        subscription_tier=user.subscription_tier,
        is_verified=user.is_verified,
        created_at=user.created_at,
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if await is_rate_limited(f"auth:login:{ip}", max_attempts=5, window_seconds=900):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Try again in 15 minutes.")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # same error for wrong email or wrong password — no user enumeration
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    response.set_cookie(key=REFRESH_COOKIE, value=refresh_token, **COOKIE_OPTS)

    return TokenResponse(access_token=access_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    db: AsyncSession = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")

    try:
        user_id = decode_token(refresh_token, "refresh")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    new_access = create_access_token(str(user.id))
    new_refresh = create_refresh_token(str(user.id))
    response.set_cookie(key=REFRESH_COOKIE, value=new_refresh, **COOKIE_OPTS)

    return TokenResponse(access_token=new_access)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=REFRESH_COOKIE)
    return {"message": "Logged out"}


@router.get("/verify-email/{token}")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    from app.core.ratelimit import get_redis
    r = get_redis()
    user_id = await r.get(f"verify:{token}")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_verified = True
    await db.commit()
    await r.delete(f"verify:{token}")

    return {"message": "Email verified successfully"}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=str(user.id),
        email=user.email,
        subscription_tier=user.subscription_tier,
        is_verified=user.is_verified,
        created_at=user.created_at,
    )
