from psycopg import Connection


def create_user(conn: Connection, email: str, password_hash: str) -> dict | None:
    """Return the new user, or None if the email is already registered."""
    return conn.execute(
        """
        INSERT INTO users (email, password_hash)
        VALUES (%s, %s)
        ON CONFLICT (email) DO NOTHING
        RETURNING id, email, created_at
        """,
        (email, password_hash),
    ).fetchone()


def get_user_by_email(conn: Connection, email: str) -> dict | None:
    return conn.execute(
        "SELECT id, email, password_hash FROM users WHERE email = %s", (email,)
    ).fetchone()
