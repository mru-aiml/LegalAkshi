"""Performance pass: bounded queries, timing headers, pool reuse.

Correctness first: every test below also asserts unchanged response
semantics. No wall-clock assertions (CI machines vary) — bounds are on
repository-call counts and response shape.
"""
from __future__ import annotations


def _seed(repo, inspections=4, products=2):
    from app.engine import engine as engine_mod
    from app.engine.facts import ASSUMED

    for i in range(inspections):
        insp = repo.create_inspection(
            {"inspector_id": "P-1", "inspector_name": "Perf",
             "business_name": f"Shop {i}", "inspection_date": "2026-09-15"})
        for j in range(products):
            prod = repo.add_product(insp["inspection_id"], {
                "product_name": f"Perf {i}-{j}", "category": "GENERAL",
                "is_prepackaged": True, "manufacturer": "Perf Foods",
                "quantity": 100, "quantity_unit": "g",
                "quantity_type": "weight",
                "manufacturing_date": "2024-05-01", "mrp": "50",
                "consumer_care": "1800-111-2222",
                "unit_sale_price": "Rs.714 per kg",
                "barcode": f"899{i}{j}000000", "imported": False,
                "ecommerce": False, "food": True})
            pid = prod["product_id"]
            engine_mod.analyze(
                repo, insp, repo.get_product(pid),
                repo.declarations_for(pid),
                as_of="2026-09-15", default_origin=ASSUMED)


def _counted(repo):
    calls = {"scoring_policy": 0, "results_for": 0, "violations_for": 0,
             "products_for_many": 0, "results_for_many": 0,
             "violations_for_many": 0, "get_product": 0}

    def _wrap(name, fn):
        def inner(*args, **kwargs):
            calls[name] += 1
            return fn(*args, **kwargs)
        return inner

    repo.scoring_policy = _wrap("scoring_policy", repo.scoring_policy)
    repo.results_for = _wrap("results_for", repo.results_for)
    repo.violations_for = _wrap("violations_for", repo.violations_for)
    repo.products_for_many = _wrap("products_for_many",
                                   repo.products_for_many)
    repo.results_for_many = _wrap("results_for_many",
                                  repo.results_for_many)
    repo.violations_for_many = _wrap("violations_for_many",
                                     repo.violations_for_many)
    repo.get_product = _wrap("get_product", repo.get_product)
    return calls


def test_overview_bounded_queries_same_semantics():
    from conftest import make_client, make_repo

    repo = make_repo()
    _seed(repo, inspections=4, products=2)  # 8 products
    calls = _counted(repo)
    client = make_client(repo)
    body = client.get("/api/v1/consumer/overview").json()
    # Semantics unchanged: every seeded product inspected with results.
    assert body["products_checked"] == 8
    assert body["verified_products"] == 8
    assert len(body["recent_checks"]) == 5
    # Constant route-level reads per request regardless of data size:
    # one policy lookup plus one batched products/results/violations
    # read each (on Postgres each is a single query; the memory repo
    # delegates internally). No per-product get_product from the route.
    assert calls["scoring_policy"] == 1, calls
    assert calls["products_for_many"] == 1, calls
    assert calls["results_for_many"] == 1, calls
    assert calls["violations_for_many"] == 1, calls
    assert calls["get_product"] == 0, calls


def test_lookup_single_policy_lookup():
    from conftest import make_client, make_repo

    repo = make_repo()
    _seed(repo, inspections=2, products=3)  # 6 matches for "Perf"
    calls = _counted(repo)
    client = make_client(repo)
    body = client.get("/api/v1/consumer/products/lookup",
                      params={"product_name": "Perf"}).json()
    assert body["count"] == 6
    assert all(m["status"] == "VERIFIED" for m in body["matches"])
    assert calls["scoring_policy"] == 1, calls


def test_timing_header_on_read_endpoints():
    from conftest import make_client, make_repo

    repo = make_repo()
    _seed(repo, inspections=1, products=1)
    client = make_client(repo)
    paths = ["/api/v1/consumer/overview",
             "/api/v1/consumer/products/lookup?product_name=Perf",
             "/api/v1/inspections", "/api/v1/rules", "/api/v1/complaints"]
    for path in paths:
        res = client.get(path)
        assert res.status_code == 200, path
        value = res.headers.get("x-request-duration-ms")
        assert value is not None, path
        assert float(value) >= 0, path


def test_timing_header_carries_no_sensitive_data():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    res = client.get("/api/v1/consumer/overview")
    assert res.status_code == 200
    # Header is a bare millisecond number — no tokens, DSN, or payload.
    assert res.headers.get("x-request-duration-ms", "").replace(
        ".", "", 1).isdigit()


def test_postgres_pool_reused_and_close_safe():
    import pytest

    psycopg_pool = pytest.importorskip("psycopg_pool")
    assert psycopg_pool.ConnectionPool is not None
    from app.repositories.postgres import PostgresRepo

    repo = PostgresRepo.__new__(PostgresRepo)
    repo._dsn = "postgresql://localhost:5432/unused"
    repo._pool = None
    first = repo._pool_or_none()
    assert first is not None  # pool constructs lazily, no server needed
    assert repo._pool_or_none() is first  # same pool reused, not rebuilt
    repo.close()  # safe without a live server
    assert repo._pool is None
