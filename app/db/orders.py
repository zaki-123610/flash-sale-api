from psycopg import Connection

from app.core.errors import AppError

ORDER_COLUMNS = """
    o.id, o.product_id, p.name AS product_name, o.quantity,
    o.unit_price_cents, o.total_cents, o.created_at
"""

# INSERT ... SELECT inserts nothing when the product does not exist, and
# ON CONFLICT inserts nothing when this user already used the key.
CLAIM_IDEMPOTENCY_KEY = """
    INSERT INTO orders (user_id, product_id, quantity, unit_price_cents, idempotency_key)
    SELECT %(user_id)s, id, %(quantity)s, price_cents, %(key)s
    FROM products
    WHERE id = %(product_id)s
    ON CONFLICT (user_id, idempotency_key) DO NOTHING
    RETURNING id, product_id, quantity, unit_price_cents, total_cents, created_at
"""

TAKE_STOCK = """
    UPDATE products
    SET stock = stock - %(quantity)s
    WHERE id = %(product_id)s AND stock >= %(quantity)s
    RETURNING name, stock
"""

FIND_BY_IDEMPOTENCY_KEY = f"""
    SELECT {ORDER_COLUMNS}
    FROM orders o JOIN products p ON p.id = o.product_id
    WHERE o.user_id = %(user_id)s AND o.idempotency_key = %(key)s
"""


def place_order(
    conn: Connection, user_id: int, product_id: int, quantity: int, idempotency_key: str
) -> tuple[dict, bool]:
    """Create an order and take its stock in a single transaction.

    Returns (order, created). created is False when this Idempotency-Key was already
    used for the same request: the original order is returned and stock is untouched.
    """
    params = {
        "user_id": user_id,
        "product_id": product_id,
        "quantity": quantity,
        "key": idempotency_key,
    }
    with conn.transaction():
        # A concurrent request with the same key blocks here on the unique index
        # until this transaction commits (-> replay) or rolls back (-> it proceeds).
        order = conn.execute(CLAIM_IDEMPOTENCY_KEY, params).fetchone()
        if order is None:
            existing = conn.execute(FIND_BY_IDEMPOTENCY_KEY, params).fetchone()
            if existing is None:
                raise AppError(404, "product_not_found", f"Product {product_id} does not exist")
            if (existing["product_id"], existing["quantity"]) != (product_id, quantity):
                raise AppError(
                    422,
                    "idempotency_key_reused",
                    "This Idempotency-Key was already used with a different request body",
                )
            return existing, False

        product = conn.execute(TAKE_STOCK, params).fetchone()
        if product is None:
            raise _out_of_stock(conn, product_id)

    return {**order, "product_name": product["name"]}, True


def _out_of_stock(conn: Connection, product_id: int) -> AppError:
    # Only used to word the error; the decision was made by TAKE_STOCK.
    stock = conn.execute("SELECT stock FROM products WHERE id = %s", (product_id,)).fetchone()
    if stock["stock"] == 0:
        return AppError(409, "sold_out", "This product is sold out")
    return AppError(
        409, "insufficient_stock", f"Not enough stock: only {stock['stock']} unit(s) left"
    )


def list_orders(conn: Connection, user_id: int) -> list[dict]:
    return conn.execute(
        f"""
        SELECT {ORDER_COLUMNS}
        FROM orders o JOIN products p ON p.id = o.product_id
        WHERE o.user_id = %s
        ORDER BY o.created_at DESC, o.id DESC
        """,
        (user_id,),
    ).fetchall()
