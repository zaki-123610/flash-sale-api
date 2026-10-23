from fastapi import APIRouter

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    verify_password,
)
from app.db import users as users_db
from app.db.pool import DbPool
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.schemas.errors import error_responses

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", status_code=201, response_model=UserOut, responses=error_responses(409, 422)
)
def register(body: RegisterIn, pool: DbPool):
    password_hash = hash_password(body.password)
    with pool.connection() as conn:
        user = users_db.create_user(conn, body.email, password_hash)
    if user is None:
        raise AppError(409, "email_taken", "This email is already registered")
    return user


@router.post("/login", response_model=TokenOut, responses=error_responses(401, 422))
def login(body: LoginIn, pool: DbPool):
    with pool.connection() as conn:
        user = users_db.get_user_by_email(conn, body.email)
    password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    if not verify_password(body.password, password_hash) or user is None:
        raise AppError(401, "invalid_credentials", "Invalid email or password")
    return TokenOut(
        access_token=create_access_token(user["id"]),
        expires_in=settings.access_token_expire_minutes * 60,
    )
