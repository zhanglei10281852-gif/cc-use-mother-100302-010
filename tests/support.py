"""测试公共辅助：固定时钟、服务工厂与记录构造。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from product_portfolio.models import parse_instant
from product_portfolio.service import PortfolioService
from product_portfolio.store import JsonStore


class FakeClock:
    def __init__(self, start: str = "2026-06-30T09:00:00+00:00"):
        self.moment = parse_instant(start)

    def __call__(self):
        return self.moment

    def advance(self, **kwargs: Any) -> None:
        self.moment += timedelta(**kwargs)


def make_service(store_path: str | None = None) -> tuple[PortfolioService, FakeClock]:
    clock = FakeClock()
    service = PortfolioService(JsonStore(store_path, clock=clock))
    service.register_product("P1", "产品一")
    service.register_product("P2", "产品二")
    service.register_config_version("P1", "A1")
    service.register_config_version("P1", "A2", supersedes="A1")
    service.register_config_version("P2", "A1")
    return service, clock


def rec(
    record_id: str,
    product: str = "P1",
    industry: str = "机器人",
    condition: str = "高负载",
    stage: str = "试装",
    version: str = "A1",
    kind: str = "feedback",
    occurred: str = "2026-03-01T08:00:00+00:00",
    source: str = "测试",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if details is None:
        details = {"channel": "trial", "summary": "试装正常"}
    return {
        "record_id": record_id,
        "product_code": product,
        "industry": industry,
        "condition": condition,
        "customer_stage": stage,
        "config_version": version,
        "kind": kind,
        "occurred_at": occurred,
        "source": source,
        "details": details,
    }
