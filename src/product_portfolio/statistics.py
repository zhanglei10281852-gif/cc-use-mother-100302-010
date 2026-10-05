"""统一统计口径：全平台唯一的指标计算入口。

所有部门、API、CLI 与评审冻结都从这里取数，避免各算各的口径。
每项指标都附带 evidence（构成该指标的记录标识），支持逐条追溯。

适配度评分 fit_score 的口径（确定性、可复算）：
    组件                权重    定义
    win_rate            0.4     已验证采用 / (已验证采用 + 失单)
    adoption_rate       0.2     已验证采用 / 机会数
    resolution_rate     0.2     已解决现场问题 / 现场问题总数
    fulfillment_rate    0.2     已兑现改进承诺 / 改进承诺总数
任一组件分母为 0 时记为 None 并在汇总时剔除，权重按剩余组件归一化；
全部组件缺失时 fit_score 为 None（数据不足，不强行打分）。
adoption_rate 可能大于 1（说明采用发生但机会漏登，原始值保留以便发现口径问题），
计算 fit_score 时各组件按 1.0 截断，保证评分落在 [0, 1]。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from .models import MarketRecord, RecordKind, parse_instant

FIT_WEIGHTS = {
    "win_rate": 0.4,
    "adoption_rate": 0.2,
    "resolution_rate": 0.2,
    "fulfillment_rate": 0.2,
}


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def compute_metrics(records: Iterable[MarketRecord], now: datetime) -> dict[str, Any]:
    """对一组记录计算统一口径指标与溯源信息。"""
    items = list(records)
    counts: dict[str, int] = {kind.value: 0 for kind in RecordKind}
    feedback_by_channel: dict[str, int] = {}
    lost_reasons: dict[str, int] = {}

    evidence: dict[str, list[str]] = {
        "opportunities": [],
        "verified_adoptions": [],
        "lost_orders": [],
        "field_issues_open": [],
        "field_issues_resolved": [],
        "commitments_promised": [],
        "commitments_fulfilled": [],
        "commitments_cancelled": [],
        "commitments_overdue": [],
    }

    for record in items:
        counts[record.kind.value] += 1
        details = record.details
        if record.kind is RecordKind.OPPORTUNITY:
            evidence["opportunities"].append(record.record_id)
        elif record.kind is RecordKind.FEEDBACK:
            channel = details["channel"]
            feedback_by_channel[channel] = feedback_by_channel.get(channel, 0) + 1
        elif record.kind is RecordKind.ADOPTION:
            if details.get("verified"):
                evidence["verified_adoptions"].append(record.record_id)
        elif record.kind is RecordKind.LOST_ORDER:
            evidence["lost_orders"].append(record.record_id)
            reason = details["reason"]
            lost_reasons[reason] = lost_reasons.get(reason, 0) + 1
        elif record.kind is RecordKind.FIELD_ISSUE:
            key = "field_issues_resolved" if details.get("status") == "resolved" else "field_issues_open"
            evidence[key].append(record.record_id)
        elif record.kind is RecordKind.IMPROVEMENT_COMMITMENT:
            status = details.get("status", "promised")
            evidence[f"commitments_{status}"].append(record.record_id)
            due = details.get("due")
            if status == "promised" and due is not None and parse_instant(due, "due") < now:
                evidence["commitments_overdue"].append(record.record_id)

    opportunities = len(evidence["opportunities"])
    verified = len(evidence["verified_adoptions"])
    lost = len(evidence["lost_orders"])
    issues_open = len(evidence["field_issues_open"])
    issues_resolved = len(evidence["field_issues_resolved"])
    promised = len(evidence["commitments_promised"])
    fulfilled = len(evidence["commitments_fulfilled"])
    cancelled = len(evidence["commitments_cancelled"])

    components = {
        "win_rate": _ratio(verified, verified + lost),
        "adoption_rate": _ratio(verified, opportunities),
        "resolution_rate": _ratio(issues_resolved, issues_open + issues_resolved),
        "fulfillment_rate": _ratio(fulfilled, promised + fulfilled + cancelled),
    }
    available = {name: min(value, 1.0) for name, value in components.items() if value is not None}
    fit_score = None
    if available:
        weight_total = sum(FIT_WEIGHTS[name] for name in available)
        fit_score = round(sum(available[name] * FIT_WEIGHTS[name] for name in available) / weight_total, 4)

    return {
        "total_records": len(items),
        "counts": counts,
        "opportunities": opportunities,
        "verified_adoptions": verified,
        "lost_orders": lost,
        "lost_reasons": dict(sorted(lost_reasons.items())),
        "feedback_by_channel": dict(sorted(feedback_by_channel.items())),
        "field_issues": {"open": issues_open, "resolved": issues_resolved},
        "commitments": {
            "promised": promised,
            "fulfilled": fulfilled,
            "cancelled": cancelled,
            "overdue": len(evidence["commitments_overdue"]),
        },
        **components,
        "fit_score": fit_score,
        "evidence": {key: sorted(value) for key, value in evidence.items()},
    }
