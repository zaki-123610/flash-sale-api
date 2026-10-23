from fastapi import APIRouter, Response

from app.core.deps import CurrentUserId, IdempotencyKey
from app.db import orders as orders_db
from app.db.pool import DbPool
from app.schemas.errors import error_responses
from app.schemas.orders import OrderCreate, OrderOut

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    status_code=201,
    response_model=OrderOut,
    responses={
        200: {
            "model": OrderOut,
            "description": "Retry with an Idempotency-Key that was already used for the same "
            "body: the original order is returned and no stock is taken.",
        },
        **error_responses(400, 401, 404, 409, 422),
    },
)
def create_order(
    user_id: CurrentUserId,
    idempotency_key: IdempotencyKey,
    body: OrderCreate,
    response: Response,
    pool: DbPool,
):
    with pool.connection() as conn:
        order, created = orders_db.place_order(
            conn, user_id, body.product_id, body.quantity, idempotency_key
        )
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return order


@router.get("", response_model=list[OrderOut], responses=error_responses(401))
def list_my_orders(user_id: CurrentUserId, pool: DbPool):
    with pool.connection() as conn:
        return orders_db.list_orders(conn, user_id)
