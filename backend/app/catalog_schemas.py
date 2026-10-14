"""Public response contracts deliberately exclude review operator and reasons."""

from typing import Any

from pydantic import BaseModel


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class Price(BaseModel):
    id: str
    price_usd: str | None
    currency: str
    price_kind: str
    dataset_updated_at: str
    observed_at: str | None
    source_name: str
    quote_status: str
    is_quotable: bool
    source_row_id: str


class Part(BaseModel):
    id: str
    category: str
    name: str
    quality_status: str
    has_price: bool
    is_quotable: bool
    quote_status: str
    selected_price_id: str | None
    price_usd: str | None
    price_metadata: Price | None


class Source(BaseModel):
    source_row_id: str
    source_name: str
    source_url: str
    dataset_updated_at: str
    input_sha256: str
    source_file: str
    row_number: int
    line_number: int
    end_line_number: int
    row_hash: str
    raw_data: dict[str, Any]


class Issue(BaseModel):
    severity: str
    field: str
    code: str
    message: str


class Detail(Part):
    specifications: dict[str, Any]
    sources: list[Source]
    issues: list[Issue]
    source_count: int
    evidence_limit: int


class Category(BaseModel):
    id: str
    name: str
    count: int


class Categories(BaseModel):
    items: list[Category]


class Batch(BaseModel):
    id: str
    name: str
    url: str
    dataset_updated_at: str
    input_sha256: str
    pipeline_version: str
    row_count: int
    currency: str
    price_kind: str


class Sources(BaseModel):
    reviewed_parts: int
    items: list[Batch]
    limit: int
