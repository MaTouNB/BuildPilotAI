from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.main import create_app
from app.models import HardwarePart, ImportBatch, PartPrice, PartSourceMap, RawImportRow

pytestmark = pytest.mark.postgres


@pytest.fixture
def catalog(database):
    # Independent synthetic source; never inserted into the real public catalog schema.
    batch = uuid4()
    ids = [UUID(int=index + 1) for index in range(5)]
    with database.begin() as db:
        db.execute(
            insert(ImportBatch),
            dict(
                id=batch,
                source_name="synthetic",
                source_url="https://example.test/fixture",
                dataset_updated_at=date(2025, 7, 23),
                input_sha256="a" * 64,
                etl_version="fixture",
                artifact_sha256="b" * 64,
                status="SUCCEEDED",
                row_count=5,
                error_count=0,
                warnings_count=0,
            ),
        )
        for index, part_id in enumerate(ids):
            db.execute(
                insert(HardwarePart),
                dict(
                    id=part_id,
                    category="GPU" if index == 1 else "CPU",
                    display_name="Same CPU"
                    if index in (0, 2)
                    else "GPU 100%_literal"
                    if index == 1
                    else "Missing CPU"
                    if index == 3
                    else "Inactive",
                    normalized_name="Same CPU"
                    if index in (0, 2)
                    else "GPU 100%_literal"
                    if index == 1
                    else "Missing CPU"
                    if index == 3
                    else "Inactive",
                    specifications={"socket": None, "source_fields": {"name": "original"}},
                    is_active=index != 4,
                    quality_status="REVIEW",
                    reviewed_by="private-operator",
                    review_reason="private-reason",
                ),
            )
            row = uuid4()
            db.execute(
                insert(RawImportRow),
                dict(
                    id=row,
                    batch_id=batch,
                    source_file="fixture/cpu.csv",
                    row_number=index + 1,
                    line_number=index + 2,
                    end_line_number=index + 2,
                    source_record_key=str(index) * 64,
                    row_hash="c" * 64,
                    raw_data={"name": "original", "price": "" if index == 3 else "10"},
                    normalized_data={},
                    normalization_status="VALID",
                    normalized_part_id=part_id,
                ),
            )
            db.execute(
                insert(PartSourceMap),
                dict(
                    id=uuid4(),
                    part_id=part_id,
                    raw_import_row_id=row,
                    source_record_key=str(index) * 64,
                    dedupe_rule="EXPLICIT",
                    review_status="REVIEWED",
                    reviewed_by="private-operator",
                    review_reason="private-reason",
                ),
            )
            db.execute(
                insert(PartPrice),
                dict(
                    id=uuid4(),
                    part_id=part_id,
                    batch_id=batch,
                    source_row_id=row,
                    price_usd=None
                    if index == 3
                    else Decimal("10.25")
                    if index == 0
                    else Decimal("20.50"),
                    currency="USD",
                    price_kind="historical_reference",
                    dataset_updated_at=date(2025, 7, 23),
                    source_name="synthetic",
                    quote_status="REVIEWED_SELECTION" if index < 2 else "VALID_REFERENCE_PRICE",
                    is_quotable=index < 2,
                    selected_by="private-operator" if index < 2 else None,
                    selection_reason="private-reason" if index < 2 else None,
                ),
            )
    with TestClient(create_app(engine=database)) as client:
        yield client, ids


def test_filters_sort_pagination_money_and_unselected(catalog):
    client, ids = catalog
    data = client.get("/api/v1/parts").json()
    assert data["total"] == 4
    cpu = client.get("/api/v1/parts?category=CPU&has_price=true").json()
    assert cpu["total"] == 2
    unselected = next(item for item in cpu["items"] if item["id"] == str(ids[2]))
    assert unselected["quote_status"] == "UNSELECTED"
    assert unselected["price_usd"] is None and unselected["has_price"]
    quoted = client.get("/api/v1/parts?quotable=true&sort=price_desc").json()
    assert [item["price_usd"] for item in quoted["items"]] == ["20.50", "10.25"]
    assert client.get("/api/v1/parts?min_price_usd=11&max_price_usd=21").json()["total"] == 1
    assert (
        client.get("/api/v1/parts?has_price=false").json()["items"][0]["quote_status"] == "MISSING"
    )
    first = client.get("/api/v1/parts?q=Same&page_size=1").json()["items"][0]
    second = client.get("/api/v1/parts?q=Same&page_size=1&page=2").json()["items"][0]
    assert first["id"] == str(ids[0]) and second["id"] == str(ids[2])


def test_source_detail_history_and_no_private_review(catalog):
    client, ids = catalog
    detail = client.get(f"/api/v1/parts/{ids[0]}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["specifications"]["socket"] is None
    assert data["sources"][0]["line_number"] == 2
    assert "private-operator" not in detail.text and "private-reason" not in detail.text
    prices = client.get(f"/api/v1/parts/{ids[0]}/prices").json()
    assert prices["items"][0]["price_usd"] == "10.25"
    assert prices["items"][0]["observed_at"] is None
    assert prices["items"][0]["dataset_updated_at"] == "2025-07-23"
    assert "private-operator" not in str(prices)
    categories = client.get("/api/v1/catalog/categories").json()["items"]
    assert next(item for item in categories if item["id"] == "CPU")["count"] == 3
    sources = client.get("/api/v1/data-sources").json()
    assert sources["reviewed_parts"] == 4 and sources["items"][0]["row_count"] == 5


@pytest.mark.parametrize(
    "query",
    [
        "page_size=101",
        "page=0",
        "page=10001",
        "sort=drop",
        "category=HACK",
        "min_price_usd=NaN",
        "max_price_usd=Infinity",
        "min_price_usd=0",
        "min_price_usd=20&max_price_usd=10",
        "min_price_usd=1.123",
        "q=" + "x" * 121,
    ],
)
def test_invalid_query(catalog, query):
    client, _ = catalog
    response = client.get("/api/v1/parts?" + query)
    assert response.status_code == 422
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_sql_injection_literal_wildcards_and_missing(catalog):
    client, ids = catalog
    assert client.get("/api/v1/parts", params={"q": "' OR 1=1 --"}).json()["total"] == 0
    assert client.get("/api/v1/parts", params={"q": "%_"}).json()["total"] == 1
    assert client.get("/api/v1/parts", params={"q": "cpu"}).json()["total"] == 3
    assert client.get(f"/api/v1/parts/{uuid4()}").status_code == 404
    assert client.get(f"/api/v1/parts/{ids[4]}").status_code == 404
    assert client.get("/api/v1/parts/invalid-uuid").status_code == 422


def test_empty_catalog(database):
    with TestClient(create_app(engine=database)) as client:
        assert client.get("/api/v1/parts").json()["items"] == []
        assert client.get("/api/v1/data-sources").json()["reviewed_parts"] == 0


def test_price_conflict_is_not_implicitly_selected(catalog, database):
    from sqlalchemy import select

    client, ids = catalog
    with database.begin() as db:
        source = dict(
            db.execute(
                select(RawImportRow.__table__).where(RawImportRow.normalized_part_id == ids[2])
            )
            .mappings()
            .one()
        )
        old_row = source["id"]
        source.update(id=uuid4(), row_number=99, line_number=100, end_line_number=100)
        db.execute(insert(RawImportRow), source)
        price = dict(
            db.execute(select(PartPrice.__table__).where(PartPrice.source_row_id == old_row))
            .mappings()
            .one()
        )
        price.update(id=uuid4(), source_row_id=source["id"], price_usd=Decimal("30.75"))
        db.execute(insert(PartPrice), price)
    detail = client.get(f"/api/v1/parts/{ids[2]}").json()
    assert detail["has_price"] and not detail["is_quotable"]
    assert detail["price_usd"] is None and detail["quote_status"] == "CONFLICT_REVIEW"
    history = client.get(f"/api/v1/parts/{ids[2]}/prices").json()
    assert {item["price_usd"] for item in history["items"]} == {"20.50", "30.75"}
