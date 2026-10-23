"""Race tests against the real app served by uvicorn in its own process."""

import asyncio
import random
from collections import Counter
from uuid import uuid4

import httpx2
import pytest

pytestmark = pytest.mark.anyio


async def fire_simultaneously(
    base_url: str, requests: list[tuple[dict, dict]]
) -> list[httpx2.Response]:
    """POST /orders for every (headers, body), all released at the same instant."""
    limits = httpx2.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx2.AsyncClient(base_url=base_url, limits=limits, timeout=60) as client:
        # Open one connection per request up front so no request waits on a TCP handshake.
        await asyncio.gather(*(client.get("/health") for _ in requests))
        start = asyncio.Event()

        async def send(headers: dict, body: dict) -> httpx2.Response:
            await start.wait()
            return await client.post("/orders", json=body, headers=headers)

        tasks = [asyncio.create_task(send(headers, body)) for headers, body in requests]
        await asyncio.sleep(0.1)
        start.set()
        return await asyncio.gather(*tasks)


def with_key(headers: dict, key: str | None = None) -> dict:
    return {**headers, "Idempotency-Key": key or str(uuid4())}


async def test_200_buyers_for_10_units(live_server, buyers, make_product, stock_of, orders_in_db):
    product_id = make_product(stock=10)
    body = {"product_id": product_id, "quantity": 1}

    responses = await fire_simultaneously(live_server, [(with_key(h), body) for h in buyers])

    assert len(responses) == 200
    statuses = Counter(r.status_code for r in responses)
    assert statuses[201] == 10
    assert statuses[409] == 190
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 409} == {"sold_out"}
    assert stock_of(product_id) == 0
    orders = orders_in_db(product_id)
    assert len(orders) == 10
    assert len({o["user_id"] for o in orders}) == 10
    assert {o["id"] for o in orders} == {r.json()["id"] for r in responses if r.status_code == 201}


async def test_200_buyers_for_the_last_unit(live_server, buyers, make_product, stock_of, orders_in_db):
    product_id = make_product(stock=1)
    body = {"product_id": product_id, "quantity": 1}

    responses = await fire_simultaneously(live_server, [(with_key(h), body) for h in buyers])

    assert Counter(r.status_code for r in responses) == {201: 1, 409: 199}
    assert stock_of(product_id) == 0
    assert len(orders_in_db(product_id)) == 1


async def test_mixed_quantities_never_oversell(
    live_server, buyers, make_product, stock_of, orders_in_db
):
    initial_stock = 25
    product_id = make_product(stock=initial_stock)
    rng = random.Random(42)
    quantities = [rng.randint(1, 5) for _ in buyers]
    requests = [
        (with_key(h), {"product_id": product_id, "quantity": q}) for h, q in zip(buyers, quantities)
    ]

    responses = await fire_simultaneously(live_server, requests)

    assert {r.status_code for r in responses} <= {201, 409}
    sold = sum(q for q, r in zip(quantities, responses) if r.status_code == 201)
    final_stock = stock_of(product_id)
    assert final_stock >= 0
    assert sold + final_stock == initial_stock
    assert sum(o["quantity"] for o in orders_in_db(product_id)) == sold
    # Stock only goes down, so every rejected request asked for more than what is left.
    assert all(q > final_stock for q, r in zip(quantities, responses) if r.status_code == 409)


async def test_20_simultaneous_retries_create_one_order(
    live_server, buyers, make_product, stock_of, db
):
    product_id = make_product(stock=10)
    key = str(uuid4())
    headers = with_key(buyers[0], key)
    body = {"product_id": product_id, "quantity": 2}

    responses = await fire_simultaneously(live_server, [(headers, body)] * 20)

    assert Counter(r.status_code for r in responses) == {201: 1, 200: 19}
    assert len({r.json()["id"] for r in responses}) == 1
    assert stock_of(product_id) == 8
    count = db.execute(
        "SELECT count(*) AS n FROM orders WHERE idempotency_key = %s", (key,)
    ).fetchone()
    assert count["n"] == 1


async def test_simultaneous_same_key_with_different_bodies(
    live_server, buyers, make_product, stock_of, db
):
    product_a, product_b = make_product(stock=10), make_product(stock=10)
    key = str(uuid4())
    headers = with_key(buyers[1], key)
    body_a = {"product_id": product_a, "quantity": 1}
    body_b = {"product_id": product_b, "quantity": 1}
    requests = [(headers, body_a), (headers, body_b)] * 10

    responses = await fire_simultaneously(live_server, requests)

    orders = db.execute("SELECT * FROM orders WHERE idempotency_key = %s", (key,)).fetchall()
    assert len(orders) == 1
    winner = orders[0]["product_id"]
    by_body = {product_a: Counter(), product_b: Counter()}
    for (_, body), response in zip(requests, responses):
        by_body[body["product_id"]][response.status_code] += 1
    loser = product_b if winner == product_a else product_a
    assert by_body[winner] == {201: 1, 200: 9}
    assert by_body[loser] == {422: 10}
    assert stock_of(winner) == 9
    assert stock_of(loser) == 10
