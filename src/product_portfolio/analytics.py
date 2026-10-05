"""市场适配度统计：确定性指标计算，每项指标都可追溯到具体记录。

评分公式（有意保持简单透明，便于评审时复核）：

- win_rate  = 验证通过的采用结果数 / (采用结果数 + 失单数)，无结果记录时为 None
- issue_load = 严重度加权现场问题数 / max(采用结果数, 1)
- fit_score = clamp(round(100 * win_rate - 10 * issue_load), 0, 100)
  没有任何采用/失单记录时返回 None，表示样本不足，不强行打分。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .models import (
    CommitmentStatus,
    FeedbackKind,
    FeedbackRecord,
    SEVERITY_WEIGHT,
    dimension_key,
)


def _ids(records: Iterable[FeedbackRecord]) -> list[str]:
    return sorted(r.record_id for r in records)


def compute_metrics(records: Iterable[FeedbackRecord]) -> dict[str, Any]:
    """对一组记录计算适配度指标，并给出每项指标的证据记录标识。"""
    items = list(records)
    adoptions = [r for r in items if r.kind == FeedbackKind.ADOPTION.value]
    lost = [r for r in items if r.kind == FeedbackKind.LOST_ORDER.value]
    issues = [r for r in items if r.kind == FeedbackKind.FIELD_ISSUE.value]
    commitments = [r for r in items if r.kind == FeedbackKind.IMPROVEMENT.value]
    open_commitments = [
        r for r in commitments if r.details.get("status") != CommitmentStatus.DONE.value
    ]

    severity_weighted = sum(SEVERITY_WEIGHT[r.details["severity"]] for r in issues)

    outcomes = len(adoptions) + len(lost)
    if outcomes:
        win_rate = len(adoptions) / outcomes
        issue_load = severity_weighted / max(len(adoptions), 1)
        fit_score: int | None = max(0, min(100, round(100 * win_rate - 10 * issue_load)))
    else:
        win_rate = None
        issue_load = None
        fit_score = None

    return {
        "record_count": len(items),
        "verified_adoptions": len(adoptions),
        "lost_orders": len(lost),
        "field_issues": len(issues),
        "severity_weighted_issues": severity_weighted,
        "open_commitments": len(open_commitments),
        "win_rate": win_rate,
        "issue_load": issue_load,
        "fit_score": fit_score,
        "evidence": {
            "verified_adoptions": _ids(adoptions),
            "lost_orders": _ids(lost),
            "field_issues": _ids(issues),
            "open_commitments": _ids(open_commitments),
        },
    }


def compare_segments(
    records: Iterable[FeedbackRecord],
    *,
    product_codes: Iterable[str] | None = None,
    industry: str | None = None,
    conditions: str | None = None,
    customer_stage: str | None = None,
    config_version: str | None = None,
) -> list[dict[str, Any]]:
    """按 产品 × 行业 × 工况 分组对比适配度，严格限定在所选细分市场内。

    每条记录只属于一个细分市场分组，跨行业套用结论在数据结构上不可能发生。
    """
    allowed_products = (
        {dimension_key(code) for code in product_codes} if product_codes is not None else None
    )
    groups: dict[tuple[str, str, str], list[FeedbackRecord]] = {}
    display: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for record in records:
        if allowed_products is not None and dimension_key(record.product_code) not in allowed_products:
            continue
        if not record.matches(
            industry=industry,
            conditions=conditions,
            customer_stage=customer_stage,
            config_version=config_version,
        ):
            continue
        key = (
            dimension_key(record.product_code),
            dimension_key(record.industry),
            dimension_key(record.conditions),
        )
        groups.setdefault(key, []).append(record)
        display[key] = (record.product_code, record.industry, record.conditions)

    rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        product_code, ind, cond = display[key]
        metrics = compute_metrics(groups[key])
        rows.append(
            {
                "product_code": product_code,
                "industry": ind,
                "conditions": cond,
                **metrics,
            }
        )
    return rows


def metrics_from_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    """持久化反序列化辅助：指标字典原样返回（浅拷贝）。"""
    return dict(data)
