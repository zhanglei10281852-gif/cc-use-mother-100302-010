"""测试共享夹具：确定性时钟、预置产品与配置版本、记录构造器。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from product_portfolio import InMemoryStore, PortfolioService


def ticking_clock(start: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)):
    """每次调用前进一小时的确定性时钟。"""
    state = {"now": start}

    def clock() -> datetime:
        current = state["now"]
        state["now"] = current + timedelta(hours=1)
        return current

    return clock


def make_service() -> PortfolioService:
    service = PortfolioService(InMemoryStore(), clock=ticking_clock())
    service.add_product("P1", "精密减速器 1 号", "2025-10-01")
    service.add_product("P2", "精密减速器 2 号", "2025-11-01")
    service.add_config_version("P1", "V1", "2025-10-01")
    service.add_config_version("P1", "V2", "2026-03-01", supersedes="V1")
    service.add_config_version("P2", "V1", "2025-11-01")
    return service


_DEFAULT_DETAILS = {
    "adoption": {"verification_ref": "PO-2026-001"},
    "lost_order": {"reason": "价格高于竞品"},
    "field_issue": {"severity": "medium"},
    "improvement_commitment": {"owner": "产品经理", "status": "open"},
}


def make_record(
    record_id: str,
    *,
    product: str = "P1",
    industry: str = "机器人",
    conditions: str = "高频往复",
    stage: str = "trial",
    version: str = "V1",
    kind: str = "adoption",
    occurred: str = "2026-01-05",
    source: str = "行业组",
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    opportunity: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "record_id": record_id,
        "product_code": product,
        "industry": industry,
        "conditions": conditions,
        "customer_stage": stage,
        "config_version": version,
        "kind": kind,
        "occurred_at": occurred,
        "source": source,
        "summary": summary or f"{record_id} 摘要",
        "details": details if details is not None else dict(_DEFAULT_DETAILS[kind]),
    }
    if opportunity is not None:
        payload["opportunity_code"] = opportunity
    return payload


def make_opportunity(
    code: str,
    *,
    product: str = "P1",
    industry: str = "机器人",
    conditions: str = "高频往复",
    stage: str = "trial",
    version: str = "V1",
    source: str = "行业组",
) -> dict[str, Any]:
    return {
        "opportunity_code": code,
        "product_code": product,
        "industry": industry,
        "conditions": conditions,
        "customer_stage": stage,
        "config_version": version,
        "source": source,
    }
