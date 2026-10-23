from typing import Annotated

import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AppError
from app.core.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> int:
    if credentials is None:
        raise AppError(401, "not_authenticated", "Missing bearer token", UNAUTHORIZED_HEADERS)
    try:
        return decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise AppError(401, "invalid_token", "Invalid or expired token", UNAUTHORIZED_HEADERS)


def get_idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Unique value per order attempt (e.g. a UUID). Reuse it when retrying.",
        ),
    ] = None,
) -> str:
    if idempotency_key is None:
        raise AppError(400, "missing_idempotency_key", "The Idempotency-Key header is required")
    key = idempotency_key.strip()
    if not 1 <= len(key) <= 255:
        raise AppError(
            400, "invalid_idempotency_key", "Idempotency-Key must be 1 to 255 characters"
        )
    return key


CurrentUserId = Annotated[int, Depends(get_current_user_id)]
IdempotencyKey = Annotated[str, Depends(get_idempotency_key)]
