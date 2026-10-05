"""新品市场适配复盘平台端到端冒烟演示。

演示完整闭环：登记产品与配置版本 → 幂等导入反馈 → 冻结评审 →
迟到数据触发修订 → 决策 → 跨细分市场对比与结论追溯。
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from product_portfolio import InMemoryStore, PortfolioService


def main() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ticks = iter(range(1000))
    clock = lambda: start + timedelta(hours=next(ticks))  # noqa: E731 - 演示用确定性时钟

    service = PortfolioService(InMemoryStore(), clock=clock)
    service.add_product("P-REDUCER-01", "精密减速器 1 号", "2025-10-01")
    service.add_config_version("P-REDUCER-01", "V1", "2025-10-01")
    service.add_config_version("P-REDUCER-01", "V2", "2026-03-01", supersedes="V1")

    records = [
        {
            "record_id": "FB-001",
            "product_code": "P-REDUCER-01",
            "industry": "机器人",
            "conditions": "高频往复",
            "customer_stage": "trial",
            "config_version": "V1",
            "kind": "adoption",
            "occurred_at": "2025-11-10",
            "source": "机器人行业组",
            "summary": "试装通过并下单",
            "details": {"verification_ref": "PO-2025-118"},
        },
        {
            "record_id": "FB-002",
            "product_code": "P-REDUCER-01",
            "industry": "风电",
            "conditions": "海上高盐雾",
            "customer_stage": "quote",
            "config_version": "V1",
            "kind": "field_issue",
            "occurred_at": "2025-12-02",
            "source": "风电行业组",
            "summary": "盐雾试验密封失效",
            "details": {"severity": "high"},
        },
    ]
    first = service.import_records(records)
    second = service.import_records(records)  # 重复导入：全部判重，统计不变

    review = service.create_review("P-REDUCER-01", industry="机器人")
    review = service.add_conclusion(
        review.review_id, "机器人高频往复工况试装转化成立", "fact", ["FB-001"]
    )
    review = service.decide(review.review_id, "continue", "试装结果可验证，继续推广")

    late = {
        **records[0],
        "record_id": "FB-003",
        "occurred_at": "2026-01-15",
        "summary": "第二家机器人客户批量采用",
        "details": {"verification_ref": "PO-2026-007"},
    }
    service.import_records([late])  # 迟到数据：不改写已冻结修订
    revised = service.revise_review(review.review_id, "迟到采用结果到达，重新冻结样本")

    output = {
        "first_import": first.to_dict(),
        "duplicate_import": second.to_dict(),
        "review_after_decision": {
            "revision": 1,
            "decision": revised.revisions[0].decision.kind,
            "sample": list(revised.revisions[0].sample_ids),
        },
        "revision_2_sample": list(revised.revisions[1].sample_ids),
        "compare": service.compare(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
