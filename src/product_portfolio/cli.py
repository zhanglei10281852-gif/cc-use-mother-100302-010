"""命令行接口：管理层与业务团队的操作入口。

用法示例：
    python -m product_portfolio.cli --store data/portfolio.json add-product NPR-01 "新一代行星减速器"
    python -m product_portfolio.cli --store data/portfolio.json import records.json
    python -m product_portfolio.cli --store data/portfolio.json compare --group-by industry
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .models import SegmentFilter
from .service import GROUP_BY_FIELDS, PortfolioService


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _scope_from_args(args: argparse.Namespace) -> SegmentFilter:
    return SegmentFilter(
        industry=getattr(args, "industry", None),
        condition=getattr(args, "condition", None),
        customer_stage=getattr(args, "customer_stage", None),
        config_version=getattr(args, "config_version", None),
    )


def _add_scope_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--industry", help="行业，如 机器人 / 风电 / 港口")
    parser.add_argument("--condition", help="工况，如 高负载 / 冲击载荷")
    parser.add_argument("--customer-stage", help="客户阶段，如 试装 / 报价 / 首单 / 复购")
    parser.add_argument("--config-version", help="配置版本")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="product-portfolio", description="新品市场适配复盘平台命令行")
    parser.add_argument("--store", default="portfolio-store.json", help="JSON 存储文件路径（默认 ./portfolio-store.json）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-product", help="登记新品")
    p.add_argument("product_code")
    p.add_argument("name")
    p.add_argument("--family", default="")
    p.add_argument("--launched-at")

    p = sub.add_parser("add-config", help="登记配置版本（升级时旧反馈保留在原版本）")
    p.add_argument("product_code")
    p.add_argument("version")
    p.add_argument("--supersedes")
    p.add_argument("--note", default="")

    p = sub.add_parser("import", help="批量导入记录（幂等：重复导入不改变统计）")
    p.add_argument("file", help="记录 JSON 文件（数组或含 records 数组的对象）")

    p = sub.add_parser("timeline", help="查看统一时间线")
    p.add_argument("product_code")
    _add_scope_flags(p)

    p = sub.add_parser("metrics", help="查看统一口径指标")
    p.add_argument("product_code")
    _add_scope_flags(p)

    p = sub.add_parser("review-freeze", help="开启阶段评审并冻结当时样本")
    p.add_argument("product_code")
    p.add_argument("gate")
    _add_scope_flags(p)

    p = sub.add_parser("review-finding", help="登记评审结论（事实/推断/假设）")
    p.add_argument("review_id")
    p.add_argument("--kind", required=True, choices=["fact", "inference", "hypothesis"])
    p.add_argument("--text", required=True)
    p.add_argument("--evidence", default="", help="逗号分隔的记录标识")
    p.add_argument("--verification-plan")
    _add_scope_flags(p)

    p = sub.add_parser("review-decide", help="登记阶段决策")
    p.add_argument("review_id")
    p.add_argument("--action", required=True, choices=["continue", "restrict", "improve", "exit"])
    p.add_argument("--rationale", required=True)
    p.add_argument("--restrict", action="append", default=[], help="受限细分，格式 industry=风电 等，可多次")
    p.add_argument("--commitments", default="", help="逗号分隔的改进承诺记录标识")

    p = sub.add_parser("review-show", help="查看评审（含迟到数据与修订链）")
    p.add_argument("review_id")

    p = sub.add_parser("review-revise", help="基于迟到数据生成评审修订版本")
    p.add_argument("review_id")

    p = sub.add_parser("compare", help="对比各新品在不同细分市场的适配度")
    p.add_argument("--group-by", choices=GROUP_BY_FIELDS)
    _add_scope_flags(p)

    p = sub.add_parser("finding-evidence", help="追溯某项结论采用的记录")
    p.add_argument("finding_id")

    return parser


def _parse_restriction(text: str) -> SegmentFilter:
    parts = {}
    for pair in text.split(","):
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"受限细分格式应为 维度=取值: {text!r}")
        parts[key.strip()] = value.strip()
    return SegmentFilter.from_dict(parts)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = PortfolioService.open(args.store)

    if args.command == "add-product":
        _print(service.register_product(args.product_code, args.name, family=args.family, launched_at=args.launched_at))
    elif args.command == "add-config":
        _print(service.register_config_version(args.product_code, args.version, supersedes=args.supersedes, note=args.note))
    elif args.command == "import":
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        records = payload["records"] if isinstance(payload, dict) else payload
        _print(service.import_records(records).to_dict())
    elif args.command == "timeline":
        _print({"entries": service.timeline(args.product_code, _scope_from_args(args))})
    elif args.command == "metrics":
        _print(service.metrics(args.product_code, _scope_from_args(args)))
    elif args.command == "review-freeze":
        _print(service.freeze_review(args.product_code, args.gate, _scope_from_args(args)).to_dict())
    elif args.command == "review-finding":
        evidence = [item for item in args.evidence.split(",") if item.strip()]
        scope = _scope_from_args(args)
        _print(
            service.add_finding(
                args.review_id,
                kind=args.kind,
                text=args.text,
                evidence=evidence,
                segment_scope=scope if scope.to_dict() else None,
                verification_plan=args.verification_plan,
            ).to_dict()
        )
    elif args.command == "review-decide":
        restrictions = [_parse_restriction(item) for item in args.restrict]
        commitments = [item for item in args.commitments.split(",") if item.strip()]
        _print(
            service.decide(
                args.review_id,
                action=args.action,
                rationale=args.rationale,
                restrictions=restrictions,
                commitment_ids=commitments,
            ).to_dict()
        )
    elif args.command == "review-show":
        _print(service.review_view(args.review_id))
    elif args.command == "review-revise":
        _print(service.revise_review(args.review_id).to_dict())
    elif args.command == "compare":
        _print(service.compare(group_by=args.group_by, scope=_scope_from_args(args)))
    elif args.command == "finding-evidence":
        _print(service.finding_evidence(args.finding_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
