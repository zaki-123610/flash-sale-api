from typing import Annotated

from fastapi import APIRouter, Path

from app.core.errors import AppError
from app.db import products as products_db
from app.db.pool import DbPool
from app.schemas.errors import error_responses
from app.schemas.products import ProductOut

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductOut])
def list_products(pool: DbPool):
    with pool.connection() as conn:
        return products_db.list_products(conn)


@router.get("/{product_id}", response_model=ProductOut, responses=error_responses(404, 422))
def get_product(product_id: Annotated[int, Path(gt=0)], pool: DbPool):
    with pool.connection() as conn:
        product = products_db.get_product(conn, product_id)
    if product is None:
        raise AppError(404, "product_not_found", f"Product {product_id} does not exist")
    return product
