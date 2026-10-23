from uuid import uuid4

import psycopg
import pytest


def place_order(client, user, product_id, quantity=1, key=None):
    return client.post(
        "/orders",
        json={"product_id": product_id, "quantity": quantity},
        headers={**user["headers"], "Idempotency-Key": key or str(uuid4())},
    )


def test_order_takes_stock_and_returns_201(client, make_user, make_product, stock_of):
    user = make_user()
    product_id = make_product(stock=10, price_cents=2500)

    response = place_order(client, user, product_id, quantity=3)

    assert response.status_code == 201
    order = response.json()
    assert order["product_id"] == product_id
    assert order["quantity"] == 3
    assert order["unit_price_cents"] == 2500
    assert order["total_cents"] == 7500
    assert stock_of(product_id) == 7


def test_max_quantity_per_order_is_accepted(client, make_user, make_product, stock_of):
    product_id = make_product(stock=5)

    response = place_order(client, make_user(), product_id, quantity=5)

    assert response.status_code == 201
    assert stock_of(product_id) == 0


def test_last_unit_then_sold_out(client, make_user, make_product, stock_of, orders_in_db):
    product_id = make_product(stock=1)

    first = place_order(client, make_user(), product_id)
    second = place_order(client, make_user(), product_id)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"] == {"code": "sold_out", "message": "This product is sold out"}
    assert stock_of(product_id) == 0
    assert len(orders_in_db(product_id)) == 1


def test_not_enough_stock_returns_409(client, make_user, make_product, stock_of, orders_in_db):
    product_id = make_product(stock=3)

    response = place_order(client, make_user(), product_id, quantity=4)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_stock"
    assert "only 3 unit(s) left" in response.json()["error"]["message"]
    assert stock_of(product_id) == 3
    assert orders_in_db(product_id) == []


def test_unknown_product_returns_404(client, make_user):
    response = place_order(client, make_user(), product_id=987654321)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"


@pytest.mark.parametrize("quantity", [0, -1, 6, 100, 2.5, "2", True, None])
def test_invalid_quantity_returns_422(
    client, make_user, make_product, stock_of, orders_in_db, quantity
):
    product_id = make_product(stock=10)

    response = place_order(client, make_user(), product_id, quantity=quantity)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"][0]["field"] == "body.quantity"
    assert stock_of(product_id) == 10
    assert orders_in_db(product_id) == []


@pytest.mark.parametrize(
    "body", [{"product_id": 1}, {"quantity": 1}, {"product_id": 1, "quantity": 1, "price": 0}]
)
def test_invalid_order_body_returns_422(client, make_user, body):
    response = client.post(
        "/orders", json=body, headers={**make_user()["headers"], "Idempotency-Key": "k"}
    )

    assert response.status_code == 422


def test_order_requires_authentication(client, make_product):
    response = client.post(
        "/orders",
        json={"product_id": make_product(stock=1), "quantity": 1},
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 401


def test_missing_idempotency_key_returns_400(client, make_user, make_product, stock_of):
    product_id = make_product(stock=5)

    response = client.post(
        "/orders",
        json={"product_id": product_id, "quantity": 1},
        headers=make_user()["headers"],
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_idempotency_key"
    assert stock_of(product_id) == 5


@pytest.mark.parametrize("key", ["   ", "x" * 256])
def test_invalid_idempotency_key_returns_400(client, make_user, make_product, key):
    response = place_order(client, make_user(), make_product(stock=5), key=key)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


def test_retry_with_same_key_returns_original_order(
    client, make_user, make_product, stock_of, orders_in_db
):
    user = make_user()
    product_id = make_product(stock=10)
    key = str(uuid4())

    first = place_order(client, user, product_id, quantity=2, key=key)
    retry = place_order(client, user, product_id, quantity=2, key=key)

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert retry.headers["idempotent-replayed"] == "true"
    assert stock_of(product_id) == 8
    assert len(orders_in_db(product_id)) == 1


def test_retry_succeeds_even_after_product_sold_out(client, make_user, make_product):
    user = make_user()
    product_id = make_product(stock=1)
    key = str(uuid4())

    first = place_order(client, user, product_id, key=key)
    retry = place_order(client, user, product_id, key=key)

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]


@pytest.mark.parametrize(
    "changed", ["quantity", "product", "unknown-product"]
)
def test_same_key_with_different_body_returns_422(
    client, make_user, make_product, stock_of, orders_in_db, changed
):
    user = make_user()
    product_id = make_product(stock=10)
    other_product_id = make_product(stock=10)
    key = str(uuid4())
    place_order(client, user, product_id, quantity=1, key=key)

    if changed == "quantity":
        response = place_order(client, user, product_id, quantity=2, key=key)
    elif changed == "product":
        response = place_order(client, user, other_product_id, quantity=1, key=key)
    else:
        response = place_order(client, user, 987654321, quantity=1, key=key)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert stock_of(product_id) == 9
    assert stock_of(other_product_id) == 10
    assert len(orders_in_db(product_id)) == 1


def test_idempotency_keys_are_scoped_per_user(client, make_user, make_product, stock_of):
    product_id = make_product(stock=10)
    key = str(uuid4())

    first = place_order(client, make_user(), product_id, key=key)
    second = place_order(client, make_user(), product_id, key=key)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert stock_of(product_id) == 8


def test_rejected_attempt_does_not_burn_the_key(client, db, make_user, make_product, stock_of):
    user = make_user()
    product_id = make_product(stock=0)
    key = str(uuid4())

    assert place_order(client, user, product_id, key=key).status_code == 409
    db.execute("UPDATE products SET stock = 1 WHERE id = %s", (product_id,))
    retry = place_order(client, user, product_id, key=key)

    assert retry.status_code == 201
    assert stock_of(product_id) == 0


def test_list_orders_returns_only_my_orders_newest_first(client, make_user, make_product):
    alice, bob = make_user(), make_user()
    product_id = make_product(stock=10)
    alice_first = place_order(client, alice, product_id, quantity=1).json()
    alice_second = place_order(client, alice, product_id, quantity=2).json()
    bob_order = place_order(client, bob, product_id, quantity=1).json()

    alice_orders = client.get("/orders", headers=alice["headers"])
    bob_orders = client.get("/orders", headers=bob["headers"])

    assert alice_orders.status_code == 200
    assert alice_orders.json() == [alice_second, alice_first]
    assert bob_orders.json() == [bob_order]


def test_list_orders_for_new_user_is_empty(client, make_user):
    response = client.get("/orders", headers=make_user()["headers"])

    assert response.status_code == 200
    assert response.json() == []


def test_database_rejects_negative_stock(db, make_product):
    product_id = make_product(stock=0)

    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE products SET stock = stock - 1 WHERE id = %s", (product_id,))
