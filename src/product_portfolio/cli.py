"""命令行接口：管理层与业务团队操作复盘平台的入口。

所有命令输出 JSON（UTF-8），便于脚本化与审计留痕。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

from .service import (
    ConflictError,
    NotFoundError,
    PortfolioService,
    StateError,
    ValidationError,
)
from .store import JsonFileStore

DEFAULT_STORE = os.environ.get("PORTFOLIO_STORE", "portfolio_store.json")


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portfolio", description="新品组合市场适配复盘平台")
    parser.add_argument("--store", default=DEFAULT_STORE, help="存储文件路径（默认 %(default)s）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-product", help="登记新品")
    p.add_argument("--code", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--launched-at", required=True, help="上市时间 ISO-8601")

    p = sub.add_parser("add-config", help="登记配置版本（升级时指定 --supersedes）")
    p.add_argument("--product", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--released-at", required=True)
    p.add_argument("--supersedes")
    p.add_argument("--note", default="")

    p = sub.add_parser("import", help="幂等导入机会与反馈记录（JSON 文件）")
    p.add_argument("--file", required=True)

    p = sub.add_parser("timeline", help="查看统一时间线")
    _add_scope_args(p, product_required=True)

    p = sub.add_parser("review-create", help="创建阶段评审并冻结样本")
    _add_scope_args(p, product_required=True)

    p = sub.add_parser("review-conclude", help="为当前修订追加结论")
    p.add_argument("--review", required=True)
    p.add_argument("--statement", required=True)
    p.add_argument("--evidence-class", required=True, choices=["fact", "inference", "hypothesis"])
    p.add_argument("--records", default="", help="逗号分隔的记录标识")

    p = sub.add_parser("review-decide", help="对当前修订作出决策")
    p.add_argument("--review", required=True)
    p.add_argument("--kind", required=True, choices=["continue", "restrict", "improve", "exit"])
    p.add_argument("--rationale", required=True)
    p.add_argument("--allowed-scope", default="", help="限制场景时的允许范围（JSON 对象）")
    p.add_argument("--improvement-records", default="", help="逗号分隔的改进承诺记录标识")

    p = sub.add_parser("review-revise", help="迟到数据触发修订")
    p.add_argument("--review", required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("review-close", help="关闭已决策的评审")
    p.add_argument("--review", required=True)

    p = sub.add_parser("review-show", help="查看评审")
    p.add_argument("--review", required=True)

    p = sub.add_parser("late", help="查看评审的迟到数据")
    p.add_argument("--review", required=True)

    p = sub.add_parser("compare", help="对比各产品在不同细分市场的适配度")
    p.add_argument("--products", default="", help="逗号分隔的产品编码，默认全部")
    _add_scope_args(p, product_required=False)

    p = sub.add_parser("trace", help="追溯某细分市场指标背后的记录")
    p.add_argument("--product", required=True)
    p.add_argument("--industry", required=True)
    p.add_argument("--conditions")
    p.add_argument("--customer-stage")
    p.add_argument("--config-version")

    p = sub.add_parser("trace-review", help="追溯评审每项结论采用的记录")
    p.add_argument("--review", required=True)

    sub.add_parser("products", help="列出全部新品")

    p = sub.add_parser("serve", help="启动 HTTP JSON API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    return parser


def _add_scope_args(p: argparse.ArgumentParser, *, product_required: bool) -> None:
    p.add_argument("--product", required=product_required)
    p.add_argument("--industry")
    p.add_argument("--conditions")
    p.add_argument("--customer-stage")
    p.add_argument("--config-version")


def _scope_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "industry": args.industry,
        "conditions": args.conditions,
        "customer_stage": args.customer_stage,
        "config_version": args.config_version,
    }


def run(args: argparse.Namespace, service: PortfolioService) -> Any:
    command = args.command
    if command == "add-product":
        return service.add_product(args.code, args.name, args.launched_at).to_dict()
    if command == "add-config":
        return service.add_config_version(
            args.product,
            args.version,
            args.released_at,
            supersedes=args.supersedes,
            note=args.note,
        ).to_dict()
    if command == "import":
        with open(args.file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        result = {
            "opportunities": service.import_opportunities(payload.get("opportunities", [])).to_dict(),
            "records": service.import_records(payload.get("records", [])).to_dict(),
        }
        return result
    if command == "timeline":
        return [r.to_dict() for r in service.timeline(args.product, **_scope_kwargs(args))]
    if command == "review-create":
        return service.create_review(args.product, **_scope_kwargs(args)).to_dict()
    if command == "review-conclude":
        return service.add_conclusion(
            args.review, args.statement, args.evidence_class, _split_csv(args.records)
        ).to_dict()
    if command == "review-decide":
        allowed_scope = json.loads(args.allowed_scope) if args.allowed_scope else None
        return service.decide(
            args.review,
            args.kind,
            args.rationale,
            allowed_scope=allowed_scope,
            improvement_record_ids=_split_csv(args.improvement_records),
        ).to_dict()
    if command == "review-revise":
        return service.revise_review(args.review, args.reason).to_dict()
    if command == "review-close":
        return service.close_review(args.review).to_dict()
    if command == "review-show":
        return service.get_review(args.review).to_dict()
    if command == "late":
        return [r.to_dict() for r in service.late_records(args.review)]
    if command == "compare":
        return service.compare(
            product_codes=_split_csv(args.products) or None, **_scope_kwargs(args)
        )
    if command == "trace":
        return service.trace(
            args.product,
            industry=args.industry,
            conditions=args.conditions,
            customer_stage=args.customer_stage,
            config_version=args.config_version,
        )
    if command == "trace-review":
        return service.trace_review(args.review)
    if command == "products":
        return [p.to_dict() for p in service.list_products()]
    if command == "serve":
        from .http_api import serve

        serve(service, host=args.host, port=args.port)
        return None
    raise ValidationError(f"未知命令: {command}")


def main(argv: Sequence[str] | None = None, *, store_path: str | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if store_path is not None:
        args.store = store_path
    service = PortfolioService(JsonFileStore(args.store))
    try:
        result = run(args, service)
    except (ValidationError, ConflictError, StateError, NotFoundError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if result is not None:
        _print({"ok": True, "data": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
