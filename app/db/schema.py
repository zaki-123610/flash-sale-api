from psycopg_pool import ConnectionPool

SCHEMA_LOCK_ID = 727_001

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        email         TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
        stock       INTEGER NOT NULL CONSTRAINT products_stock_non_negative CHECK (stock >= 0),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id          BIGINT NOT NULL REFERENCES users (id),
        product_id       BIGINT NOT NULL REFERENCES products (id),
        quantity         INTEGER NOT NULL CHECK (quantity > 0),
        unit_price_cents INTEGER NOT NULL,
        total_cents      INTEGER GENERATED ALWAYS AS (quantity * unit_price_cents) STORED,
        idempotency_key  TEXT NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT orders_user_idempotency_key_unique UNIQUE (user_id, idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS orders_product_id_idx ON orders (product_id)",
]

SEED_PRODUCTS = [
    ("Limited Edition Sneakers", "Only one pair exists. Good luck.", 19999, 1),
    ("Wireless Earbuds Pro", "Noise cancelling, 30h battery.", 7999, 5),
    ("Mechanical Keyboard", "Hot-swappable switches, RGB.", 12999, 10),
    ("Smart Watch", "Heart-rate, GPS, 7-day battery.", 24999, 50),
    ("USB-C Fast Charger", "65W GaN charger.", 2999, 200),
]


def init_db(pool: ConnectionPool) -> None:
    """Create tables and seed demo products. Safe to run from several workers at once."""
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_ID,))
        for statement in SCHEMA:
            conn.execute(statement)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO products (name, description, price_cents, stock)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO NOTHING
                """,
                SEED_PRODUCTS,
            )
