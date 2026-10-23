from psycopg import Connection

PRODUCT_COLUMNS = "id, name, description, price_cents, stock"


def list_products(conn: Connection) -> list[dict]:
    return conn.execute(f"SELECT {PRODUCT_COLUMNS} FROM products ORDER BY id").fetchall()


def get_product(conn: Connection, product_id: int) -> dict | None:
    return conn.execute(
        f"SELECT {PRODUCT_COLUMNS} FROM products WHERE id = %s", (product_id,)
    ).fetchone()
