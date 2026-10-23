import pytest

from app.db.schema import SEED_PRODUCTS


def test_demo_products_are_seeded_with_low_stock(client):
    response = client.get("/products")

    assert response.status_code == 200
    stock_by_name = {p["name"]: p["stock"] for p in response.json()}
    for name, _, _, stock in SEED_PRODUCTS:
        assert stock_by_name[name] == stock
    assert {1, 5, 10} <= set(stock_by_name.values())


def test_list_products_shows_current_stock(client, make_product):
    product_id = make_product(stock=7, price_cents=4200)

    products = client.get("/products").json()

    product = next(p for p in products if p["id"] == product_id)
    assert product["stock"] == 7
    assert product["price_cents"] == 4200
    assert product["sold_out"] is False


def test_get_product(client, make_product):
    product_id = make_product(stock=0)

    response = client.get(f"/products/{product_id}")

    assert response.status_code == 200
    assert response.json()["id"] == product_id
    assert response.json()["stock"] == 0
    assert response.json()["sold_out"] is True


def test_get_unknown_product_returns_404(client):
    response = client.get("/products/987654321")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"


@pytest.mark.parametrize("product_id", ["0", "-3", "abc"])
def test_get_product_with_invalid_id_returns_422(client, product_id):
    response = client.get(f"/products/{product_id}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
