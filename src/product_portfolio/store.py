"""存储层：内存索引 + 可选 JSON 文件持久化（原子写）。

记录一旦入库不可改写；评审冻结后同样不可变。所有修订都是新增对象，
因此历史版本可以随时回放与审计。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .models import (
    ConfigVersion,
    MarketRecord,
    Product,
    Review,
    SegmentFilter,
    format_instant,
    parse_instant,
    utcnow,
)

STORE_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """入库记录：业务内容 + 系统入库时间 + 单调序号（稳定排序用）。"""

    record: MarketRecord
    ingested_at: str
    seq: int

    def to_dict(self) -> dict[str, Any]:
        return {"record": self.record.to_dict(), "ingested_at": self.ingested_at, "seq": self.seq}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredRecord":
        return cls(
            record=MarketRecord.from_dict(data["record"]),
            ingested_at=data["ingested_at"],
            seq=data["seq"],
        )


class JsonStore:
    """领域对象仓库。path 为 None 时纯内存运行（测试与演示用）。"""

    def __init__(self, path: str | os.PathLike[str] | None = None, clock: Callable[[], Any] = utcnow):
        self.path = Path(path) if path else None
        self.clock = clock
        self.products: dict[str, Product] = {}
        self.config_versions: dict[tuple[str, str], ConfigVersion] = {}
        self.records: dict[str, StoredRecord] = {}
        self.reviews: dict[str, Review] = {}
        self.seq = 0
        if self.path and self.path.exists():
            self._load()

    # -- 持久化 -----------------------------------------------------------

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("format_version") != STORE_FORMAT_VERSION:
            raise ValueError(f"不支持的存储格式版本: {data.get('format_version')}")
        self.products = {item["product_code"]: Product.from_dict(item) for item in data.get("products", [])}
        self.config_versions = {
            (item["product_code"], item["version"]): ConfigVersion.from_dict(item)
            for item in data.get("config_versions", [])
        }
        self.records = {}
        for item in data.get("records", []):
            stored = StoredRecord.from_dict(item)
            self.records[stored.record.record_id] = stored
        self.reviews = {item["review_id"]: Review.from_dict(item) for item in data.get("reviews", [])}
        self.seq = data.get("seq", max((stored.seq for stored in self.records.values()), default=0))

    def save(self) -> None:
        """原子写：先写临时文件再替换，避免半截文件。"""
        if self.path is None:
            return
        payload = {
            "format_version": STORE_FORMAT_VERSION,
            "seq": self.seq,
            "products": [product.to_dict() for product in sorted(self.products.values(), key=lambda p: p.product_code)],
            "config_versions": [
                version.to_dict()
                for version in sorted(self.config_versions.values(), key=lambda v: (v.product_code, v.version))
            ],
            "records": [stored.to_dict() for stored in sorted(self.records.values(), key=lambda s: s.seq)],
            "reviews": [review.to_dict() for review in sorted(self.reviews.values(), key=lambda r: r.review_id)],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, self.path)

    # -- 主数据 -----------------------------------------------------------

    def add_product(self, product: Product) -> Product:
        existing = self.products.get(product.product_code)
        if existing is not None:
            if existing != product:
                raise ValueError(f"产品 {product.product_code} 已存在且内容不一致")
            return existing
        self.products[product.product_code] = product
        self.save()
        return product

    def get_product(self, product_code: str) -> Product:
        try:
            return self.products[product_code]
        except KeyError:
            raise KeyError(f"产品不存在: {product_code}") from None

    def add_config_version(self, version: ConfigVersion) -> ConfigVersion:
        self.get_product(version.product_code)
        key = (version.product_code, version.version)
        existing = self.config_versions.get(key)
        if existing is not None:
            if existing != version:
                raise ValueError(f"配置版本 {version.product_code}/{version.version} 已存在且内容不一致")
            return existing
        if version.supersedes is not None and (version.product_code, version.supersedes) not in self.config_versions:
            raise ValueError(f"被取代的版本 {version.supersedes} 尚未登记")
        self.config_versions[key] = version
        self.save()
        return version

    def has_config_version(self, product_code: str, version: str) -> bool:
        return (product_code, version) in self.config_versions

    # -- 记录 -------------------------------------------------------------

    def add_record(self, record: MarketRecord) -> StoredRecord:
        """追加记录并分配入库时间与序号。调用方负责幂等判断。"""
        self.seq += 1
        stored = StoredRecord(record=record, ingested_at=format_instant(self.clock()), seq=self.seq)
        self.records[record.record_id] = stored
        self.save()
        return stored

    def get_record(self, record_id: str) -> StoredRecord:
        try:
            return self.records[record_id]
        except KeyError:
            raise KeyError(f"记录不存在: {record_id}") from None

    def iter_records(self, product_code: str | None = None, scope: SegmentFilter | None = None) -> Iterator[StoredRecord]:
        """按统一时间线顺序（发生时间 + 入库序号）遍历记录。"""
        items = sorted(self.records.values(), key=lambda s: (s.record.occurred_at, s.seq))
        for stored in items:
            if product_code is not None and stored.record.product_code != product_code:
                continue
            if scope is not None and not scope.matches(stored.record):
                continue
            yield stored

    # -- 评审 -------------------------------------------------------------

    def add_review(self, review: Review) -> Review:
        if review.review_id in self.reviews:
            raise ValueError(f"评审已存在: {review.review_id}")
        self.reviews[review.review_id] = review
        self.save()
        return review

    def update_review(self, review: Review) -> Review:
        if review.review_id not in self.reviews:
            raise KeyError(f"评审不存在: {review.review_id}")
        self.reviews[review.review_id] = review
        self.save()
        return review

    def get_review(self, review_id: str) -> Review:
        try:
            return self.reviews[review_id]
        except KeyError:
            raise KeyError(f"评审不存在: {review_id}") from None

    def review_chain(self, product_code: str, gate: str) -> list[Review]:
        """同一产品同一阶段门的修订链，按版本号排序。"""
        chain = [r for r in self.reviews.values() if r.product_code == product_code and r.gate == gate]
        return sorted(chain, key=lambda r: r.revision_no)

    def find_finding(self, finding_id: str) -> tuple[Review, Any]:
        for review in self.reviews.values():
            for finding in review.findings:
                if finding.finding_id == finding_id:
                    return review, finding
        raise KeyError(f"结论不存在: {finding_id}")

    # -- 迟到数据 -----------------------------------------------------------

    def late_records_for(self, review: Review) -> list[StoredRecord]:
        """冻结之后才入库、但业务发生时间属于冻结时点之前的记录。

        这类记录不得改写已冻结的样本与统计，只能触发修订。
        """
        frozen_at = parse_instant(review.frozen_at, "frozen_at")
        late: list[StoredRecord] = []
        for stored in self.iter_records(review.product_code, review.scope):
            if parse_instant(stored.record.occurred_at) <= frozen_at and parse_instant(stored.ingested_at, "ingested_at") > frozen_at:
                late.append(stored)
        return late
