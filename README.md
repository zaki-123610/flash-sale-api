# Flash Sale API

Backend for a limited-time flash sale. Some products have only 1, 5 or 10 units, and hundreds of customers press "Buy" at the same moment. The API guarantees that **a product is never oversold** and that **a retried request never creates a second order**.

- **Live API:** `https://<your-service>.onrender.com` _(placeholder, add after deploying)_
- **Interactive docs:** `https://<your-service>.onrender.com/docs`

Stack: FastAPI, PostgreSQL on Neon, psycopg 3 with raw SQL (no ORM), psycopg_pool, bcrypt, JWT.

## Features

- Register and log in (bcrypt-hashed passwords, JWT bearer tokens)
- Browse products with live stock and a `sold_out` flag
- Place an order (max 5 units per order) with a required `Idempotency-Key` header
- View your own orders
- Atomic stock decrement: stock never goes negative and is never oversold, under any concurrency
- Idempotent order creation backed by a `UNIQUE` constraint: safe to retry, even concurrently
- Consistent JSON errors with machine-readable codes, never a stack trace
- Tables are created and demo products seeded on startup (stock 1, 5, 10, 50, 200)
- 62 tests, including real concurrency tests against a running server

## How overselling is prevented

Stock is taken with **one conditional SQL statement**:

```sql
UPDATE products
SET stock = stock - %(quantity)s
WHERE id = %(product_id)s AND stock >= %(quantity)s
RETURNING name, stock
```

- **No read-then-write.** The application never reads the stock to make a decision. The check and the write are the same statement.
- **Postgres serializes buyers on the row.** When 200 transactions update the same product, they queue on its row lock. Under `READ COMMITTED`, each waiting `UPDATE` re-checks `stock >= quantity` against the latest committed row once it gets the lock. The 11th buyer of a 10-unit product sees `stock = 0`, matches zero rows, and gets `409 sold_out`.
- **One transaction.** The order `INSERT` and the stock `UPDATE` run in the same transaction. If the `UPDATE` matches nothing, everything rolls back. There is never an order without stock taken, or stock taken without an order.
- **Last line of defense.** `CHECK (stock >= 0)` on `products.stock` means even a buggy query could never store a negative stock.

The guarantee comes from the database, not from the Python process. It holds with several uvicorn workers or several Render instances.

## How duplicate orders are prevented

`POST /orders` requires an `Idempotency-Key` header. The client generates one per purchase attempt (a UUID, for example) and resends the **same** key when it retries. The key is stored on the order with `UNIQUE (user_id, idempotency_key)`.

Inside a single transaction ([app/db/orders.py](app/db/orders.py)):

1. **Claim the key:**
   ```sql
   INSERT INTO orders (user_id, product_id, quantity, unit_price_cents, idempotency_key)
   SELECT %(user_id)s, id, %(quantity)s, price_cents, %(key)s FROM products WHERE id = %(product_id)s
   ON CONFLICT (user_id, idempotency_key) DO NOTHING
   RETURNING ...
   ```
2. **Take the stock** with the conditional `UPDATE` above. If it matches nothing, the whole transaction rolls back (the claim included) and the API returns `409`.
3. **Commit**, then return `201 Created`.

If step 1 inserts nothing, the key was already used (or the product does not exist). The existing order is loaded:

| Situation | Response |
|---|---|
| New key | `201` + the new order |
| Same key, same body | `200` + the **original** order, header `Idempotent-Replayed: true`, stock untouched |
| Same key, different body | `422 idempotency_key_reused` |
| Missing, blank or over-255-character key | `400` |

**Simultaneous retries.** Two requests with the same key can arrive at the same instant. Both try the `INSERT`, and the unique index lets only one in. The other request **blocks** until the first transaction finishes:
- If the first commits, the second takes the `DO NOTHING` path and returns the committed order.
- If the first rolled back (sold out), the second proceeds as a normal attempt.

No prior `SELECT` is involved in the decision, so there is no check-then-act race window.

Keys are scoped per user, so two users can never collide. Rejected attempts (`409`) are not remembered: the next use of that key is evaluated fresh.

## Endpoints

| Method | Path | Auth | Body / notes | Success |
|---|---|---|---|---|
| `POST` | `/auth/register` | | `{"email", "password"}` (8 to 72 bytes) | `201` user |
| `POST` | `/auth/login` | | `{"email", "password"}` | `200` `{access_token, token_type, expires_in}` |
| `GET` | `/products` | | | `200` list with `stock`, `sold_out` |
| `GET` | `/products/{id}` | | | `200` product |
| `POST` | `/orders` | Bearer + `Idempotency-Key` | `{"product_id", "quantity"}` (quantity 1 to 5) | `201` new, `200` replay |
| `GET` | `/orders` | Bearer | the current user's orders, newest first | `200` list |
| `GET` | `/health` | | checks the database | `200` |

Example session:

```bash
API=http://127.0.0.1:8000

curl -X POST $API/auth/register -H "Content-Type: application/json" \
  -d '{"email": "amina@example.com", "password": "flash-sale-2026"}'

TOKEN=$(curl -s -X POST $API/auth/login -H "Content-Type: application/json" \
  -d '{"email": "amina@example.com", "password": "flash-sale-2026"}' | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

curl $API/products

curl -X POST $API/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: 3f1c2a9e-7b4d-4c1e-9a55-2d8f0e6b1c77" \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "quantity": 1}'
# 201 the first time, 200 with the same order if you run it again

curl $API/orders -H "Authorization: Bearer $TOKEN"
```

## Errors

Every error has the same shape:

```json
{"error": {"code": "sold_out", "message": "This product is sold out"}}
```

Validation errors add `"details": [{"field": "body.quantity", "message": "..."}]`.

| Status | Codes | When |
|---|---|---|
| `400` | `missing_idempotency_key`, `invalid_idempotency_key` | `Idempotency-Key` header absent, blank or too long |
| `401` | `not_authenticated`, `invalid_token`, `invalid_credentials` | no or bad token, wrong email or password |
| `404` | `product_not_found`, `not_found` | unknown product, unknown route |
| `409` | `sold_out`, `insufficient_stock`, `email_taken` | no stock left, fewer units left than requested, email already registered |
| `422` | `validation_error`, `idempotency_key_reused` | quantity < 1 or > 5, non-integer, missing or unknown fields; key reused with a different body |
| `500` | `internal_error` | unexpected bug (details are logged, never returned) |
| `503` | `service_unavailable` | database unreachable or connection pool exhausted |

## Project structure

```
app/
  main.py          FastAPI app, lifespan (pool + schema + seed), /health
  core/            config, security (bcrypt, JWT), deps (auth, Idempotency-Key), errors
  db/              connection pool, schema + seed, SQL for users / products / orders
  schemas/         Pydantic request and response models
  routers/         auth, products, orders
tests/             API tests and concurrency tests
render.yaml        Render blueprint
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env               # then fill in DATABASE_URL and JWT_SECRET
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs. Tables and demo products are created on first start.

## Tests

The tests **drop and recreate all tables**, so they need their own database. With Neon, create a branch (for example `test`) in the console and put its connection string in `.env`:

```
TEST_DATABASE_URL=postgresql://...-pooler.../neondb?sslmode=require
```

A local PostgreSQL database works too. The suite refuses to run if `TEST_DATABASE_URL` equals `DATABASE_URL`.

```bash
pytest -v
```

API tests ([tests/test_auth.py](tests/test_auth.py), [tests/test_products.py](tests/test_products.py), [tests/test_orders.py](tests/test_orders.py), [tests/test_errors.py](tests/test_errors.py)) cover:
- auth: register, duplicate email, validation, login, bad, expired and forged tokens
- products: listing with stock, seed data, 404, 422
- orders: stock decrement, last unit then sold out, insufficient stock, unknown product, invalid quantities and bodies, missing or invalid key, retry returning the original order, key reused with another body, keys scoped per user, rejected attempts not burning the key, users only seeing their own orders, the `CHECK` constraint
- error format: 404, malformed JSON, 500 and 503 without internals

Concurrency tests ([tests/test_concurrency.py](tests/test_concurrency.py)) start the **real app with uvicorn in a separate process** and fire requests with `asyncio` + `httpx2`. Every request is created up front with its connection already open, then all are released at the same instant:

| Test | Assertions |
|---|---|
| 200 different users buy a 10-unit product | exactly 10 × `201`, 190 × `409 sold_out`, stock = 0, exactly 10 orders in the DB, from 10 distinct users |
| 200 users, 1 unit | 1 × `201`, 199 × `409`, stock = 0 |
| 200 users, random quantities 1 to 5, 25 units | units sold + stock left = 25, stock ≥ 0, every rejected request asked for more than was left |
| 20 simultaneous requests, same key | 1 × `201` + 19 × `200`, all with the same order id, stock taken once, one row in the DB |
| 20 simultaneous requests, same key, 2 different bodies | one order; the other body's 10 requests all get `422` |

**The concurrency tests catch real bugs.** They were checked against two deliberately broken copies of the app:
- a read-then-write stock update produced **156 orders for 10 units**;
- a SELECT-before-INSERT idempotency check produced **5 orders for one key**.

The suite failed on both.

The tests use `BCRYPT_ROUNDS=4` to stay fast. Against a remote Neon branch they take longer because of network latency, but they assert the same thing.

## Deploy on Render

1. **Neon:** create a project (region _AWS Europe Central 1 (Frankfurt)_ is closest to Render Frankfurt). Copy the **pooled** connection string from *Connect*.
2. **GitHub:** push this repository.
3. **Render:** *New → Blueprint*, pick the repository. Render reads [render.yaml](render.yaml), which defines a free Python web service in Frankfurt with health check `/health`. When asked, paste `DATABASE_URL`. `JWT_SECRET` is generated automatically.

   Or, by hand: *New → Web Service* with
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/health`
   - Environment: `DATABASE_URL`, `JWT_SECRET` (run `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
4. Deploy. The first boot creates the tables and seeds the products. Open `https://<service>.onrender.com/docs` and update the live link at the top of this README.

Python 3.13 is selected by [.python-version](.python-version). On the free plan, Render sleeps after 15 minutes without traffic, so the first request then takes up to about a minute. Neon's free compute also suspends when idle. The pool checks each connection before use and reconnects transparently.

## Configuration

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL / Neon connection string |
| `JWT_SECRET` | required | at least 32 characters |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| `BCRYPT_ROUNDS` | `12` | |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | `1` / `10` | |
| `MAX_QUANTITY_PER_ORDER` | `5` | |
| `TEST_DATABASE_URL` | | tests only |

## Design notes

- **Sync endpoints + `psycopg_pool.ConnectionPool`.** FastAPI runs `def` endpoints in its threadpool, so slow work never blocks the event loop. It also works on Windows, where psycopg's async mode can't use the default event loop. Correctness relies on PostgreSQL, not on the threads.
- **Autocommit connections, explicit transactions.** Reads don't hold transactions open. Only order placement uses `conn.transaction()`.
- **Neon-friendly.** `prepare_threshold=None` keeps psycopg compatible with Neon's PgBouncer pooler. Schema creation runs under `pg_advisory_xact_lock`, so several workers can boot at once.
- **Money in integer cents.** The unit price is copied into the order at purchase time, and `total_cents` is a generated column.
- **No user enumeration.** Login runs a bcrypt check even for unknown emails, so both cases take the same time and return the same error.
