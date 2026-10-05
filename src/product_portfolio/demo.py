"""确定性演示数据：八款新品一年内的机会、反馈、采用、失单、现场问题与改进承诺。

供命令行冒烟（run_cli.py）与集成测试复用，不依赖随机数与真实时钟。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .models import SegmentFilter
from .service import PortfolioService
from .store import JsonStore

# (编码, 名称, 产品族, 主行业)
PRODUCTS = [
    ("NPR-01", "机器人关节精密减速器", "机器人传动", "机器人"),
    ("NPR-02", "风电主齿轮箱轻量化型", "风电传动", "风电"),
    ("NPR-03", "港口起升机构减速器", "港口传动", "港口"),
    ("NPR-04", "协作机器人伺服一体机", "机器人传动", "机器人"),
    ("NPR-05", "风电偏航变桨驱动", "风电传动", "风电"),
    ("NPR-06", "港口AGV驱动单元", "港口传动", "港口"),
    ("NPR-07", "高扭矩工业齿轮箱", "通用工业", "冶金"),
    ("NPR-08", "防爆型矿山减速器", "矿山传动", "矿山"),
]

CONFIGS = [
    ("NPR-01", "A1", None), ("NPR-01", "A2", "A1"),
    ("NPR-02", "A1", None),
    ("NPR-03", "A1", None),
    ("NPR-04", "A1", None), ("NPR-04", "B1", "A1"),
    ("NPR-05", "A1", None),
    ("NPR-06", "A1", None),
    ("NPR-07", "A1", None),
    ("NPR-08", "A1", None),
]

CONDITIONS = ("高负载", "冲击载荷", "连续运行")


class DemoClock:
    """可推进的固定时钟，保证演示与测试可复现。"""

    def __init__(self, start: datetime = datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc)):
        self.moment = start

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs: Any) -> None:
        self.moment += timedelta(**kwargs)


def _record(
    seq: int,
    product: str,
    kind: str,
    industry: str,
    condition: str,
    stage: str,
    version: str,
    occurred: str,
    source: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "record_id": f"{product}-R{seq:03d}",
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


def demo_records() -> list[dict[str, Any]]:
    """构造八款新品的一年市场记录（确定性）。"""
    records: list[dict[str, Any]] = []
    for index, (code, _name, _family, industry) in enumerate(PRODUCTS, start=1):
        condition = CONDITIONS[index % len(CONDITIONS)]
        base_month = (index % 6) + 1
        seq = 1

        def add(kind: str, month: int, stage: str, source: str, details: dict[str, Any], **override: Any) -> None:
            nonlocal seq
            records.append(
                _record(
                    seq, code, kind,
                    override.get("industry", industry),
                    override.get("condition", condition),
                    stage,
                    override.get("version", "A1"),
                    f"2026-{month:02d}-15T08:00:00+00:00",
                    source,
                    details,
                )
            )
            seq += 1

        add("opportunity", base_month, "试装", "行业团队", {"expected_units": 20 + index})
        add("feedback", base_month, "试装", "试装组", {"channel": "trial", "summary": "试装运行平稳"})
        add("feedback", base_month + 1, "报价", "报价组", {"channel": "quote", "summary": "客户认为报价偏高"})
        if index % 2:
            add("adoption", base_month + 2, "首单", "销售系统", {"verified": True, "evidence": "合同与验收单齐全", "units": 10 + index})
        else:
            add("lost_order", base_month + 2, "报价", "销售系统", {"reason": "价格高于竞品"})
        if index % 3 == 0:
            add("field_issue", base_month + 3, "首单", "服务系统", {"severity": "high", "status": "open", "summary": "早期磨损超标"})
            add(
                "improvement_commitment", base_month + 3, "首单", "产品团队",
                {"text": "优化齿面热处理工艺", "status": "promised", "due": "2026-09-30T00:00:00+00:00"},
            )
        else:
            add("feedback", base_month + 3, "复购", "维护组", {"channel": "maintenance", "summary": "例行维护正常"})
        if index % 4 == 0:
            add("feedback", base_month + 4, "首单", "服务系统", {"channel": "failure", "summary": "传感器误报一次"})

    # NPR-01 跨行业验证：同一产品在风电行业的表现单独统计，不与机器人行业混算
    records.append(
        _record(
            100, "NPR-01", "opportunity", "风电", "高负载", "报价", "A1",
            "2026-04-10T08:00:00+00:00", "行业团队", {"expected_units": 12},
        )
    )
    records.append(
        _record(
            101, "NPR-01", "lost_order", "风电", "高负载", "报价", "A1",
            "2026-05-08T08:00:00+00:00", "销售系统", {"reason": "工况不匹配"},
        )
    )
    # NPR-01 配置升级：A2 上市后，A1 的旧反馈仍留在 A1 上
    records.append(
        _record(
            102, "NPR-01", "feedback", "机器人", "冲击载荷", "复购", "A2",
            "2026-08-10T08:00:00+00:00", "试装组",
            {"channel": "trial", "summary": "A2 版本温升明显改善"},
        )
    )
    records.append(
        _record(
            103, "NPR-01", "adoption", "机器人", "冲击载荷", "复购", "A2",
            "2026-09-01T08:00:00+00:00", "销售系统",
            {"verified": True, "evidence": "复购合同", "units": 30},
        )
    )
    return records


def late_record() -> dict[str, Any]:
    """一条迟到数据：业务发生在评审冻结之前，冻结后才送达。"""
    return _record(
        200, "NPR-01", "lost_order", "机器人", "冲击载荷", "报价", "A1",
        "2026-05-20T08:00:00+00:00", "销售系统",
        {"reason": "交期无法满足客户节拍"},
    )


def build_demo_service() -> tuple[PortfolioService, DemoClock]:
    clock = DemoClock()
    service = PortfolioService(JsonStore(clock=clock))
    for code, name, family, _industry in PRODUCTS:
        service.register_product(code, name, family=family, launched_at="2026-01-10T00:00:00+00:00")
    for code, version, supersedes in CONFIGS:
        service.register_config_version(code, version, supersedes=supersedes)
    return service, clock


def run_demo() -> dict[str, Any]:
    """端到端演示：导入 → 幂等重复导入 → 冻结评审 → 迟到数据 → 修订 → 对比与追溯。"""
    service, clock = build_demo_service()

    first = service.import_records(demo_records())
    second = service.import_records(demo_records())  # 重复导入：统计不变

    review = service.freeze_review("NPR-01", "G2-上市复盘", SegmentFilter(industry="机器人"))
    finding = service.add_finding(
        review.review_id,
        kind="fact",
        text="机器人行业冲击载荷工况下试装反馈稳定",
        evidence=["NPR-01-R002"],
        segment_scope=SegmentFilter(industry="机器人", condition="冲击载荷"),
    )
    service.decide(review.review_id, action="continue", rationale="已验证采用且现场问题可控")

    # 迟到数据：冻结前发生、冻结后送达，只能触发修订
    clock.advance(days=1)
    service.import_records([late_record()])
    late = service.late_records(review.review_id)
    revision = service.revise_review(review.review_id)

    comparison = service.compare(group_by="industry")
    evidence = service.finding_evidence(finding.finding_id)

    return {
        "first_import": first.to_dict()["summary"],
        "second_import": second.to_dict()["summary"],
        "review": {
            "review_id": review.review_id,
            "frozen_sample_size": len(review.sample),
            "status_after_decision": "decided",
        },
        "late_records_after_freeze": late,
        "revision": {
            "review_id": revision.review_id,
            "revision_of": revision.revision_of,
            "sample_size": len(revision.sample),
        },
        "comparison_rows": [
            {
                "product_code": row["product_code"],
                "group": row["group"],
                "verified_adoptions": row["metrics"]["verified_adoptions"],
                "lost_orders": row["metrics"]["lost_orders"],
                "fit_score": row["fit_score"],
            }
            for row in comparison["rows"]
        ],
        "finding_evidence": {
            "finding_id": evidence["finding"]["finding_id"],
            "record_ids": [record["record_id"] for record in evidence["records"]],
            "fingerprints_match": all(record["fingerprint_match"] for record in evidence["records"]),
        },
    }
