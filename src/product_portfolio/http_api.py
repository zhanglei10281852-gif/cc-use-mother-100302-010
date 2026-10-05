"""管理层 HTTP API：仅依赖标准库 http.server，与命令行共用同一服务层。

启动方式：
    python -m product_portfolio.http_api --store data/portfolio.json --port 8000
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .models import SegmentFilter
from .service import PortfolioService

Handler = Callable[["RequestContext"], Any]


class RequestContext:
    def __init__(self, service: PortfolioService, params: dict[str, str], query: dict[str, list[str]], body: Any):
        self.service = service
        self.params = params
        self.query = query
        self.body = body

    def query_one(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default


def _scope_from(data: dict[str, Any]) -> SegmentFilter:
    return SegmentFilter(
        industry=data.get("industry"),
        condition=data.get("condition"),
        customer_stage=data.get("customer_stage"),
        config_version=data.get("config_version"),
    )


def build_routes(service: PortfolioService) -> list[tuple[str, re.Pattern[str], Handler]]:
    def route(method: str, pattern: str):
        def decorator(func: Handler) -> Handler:
            routes.append((method, re.compile(f"^{pattern}$"), func))
            return func
        return decorator

    routes: list[tuple[str, re.Pattern[str], Handler]] = []

    @route("GET", r"/health")
    def health(ctx: RequestContext) -> Any:
        return {"status": "ok"}

    @route("POST", r"/products")
    def add_product(ctx: RequestContext) -> Any:
        body = ctx.body or {}
        return ctx.service.register_product(
            product_code=body.get("product_code"),
            name=body.get("name"),
            family=body.get("family", ""),
            launched_at=body.get("launched_at"),
        )

    @route("POST", r"/config-versions")
    def add_config(ctx: RequestContext) -> Any:
        body = ctx.body or {}
        return ctx.service.register_config_version(
            product_code=body.get("product_code"),
            version=body.get("version"),
            supersedes=body.get("supersedes"),
            note=body.get("note", ""),
        )

    @route("POST", r"/imports")
    def import_records(ctx: RequestContext) -> Any:
        body = ctx.body or {}
        records = body.get("records")
        if not isinstance(records, list):
            raise ValueError("请求体必须包含 records 数组")
        return ctx.service.import_records(records).to_dict()

    @route("GET", r"/timeline")
    def timeline(ctx: RequestContext) -> Any:
        product = ctx.query_one("product")
        if not product:
            raise ValueError("缺少查询参数 product")
        scope = SegmentFilter(
            industry=ctx.query_one("industry"),
            condition=ctx.query_one("condition"),
            customer_stage=ctx.query_one("customer_stage"),
            config_version=ctx.query_one("config_version"),
        )
        return {"entries": ctx.service.timeline(product, scope)}

    @route("GET", r"/metrics")
    def metrics(ctx: RequestContext) -> Any:
        product = ctx.query_one("product")
        if not product:
            raise ValueError("缺少查询参数 product")
        scope = SegmentFilter(
            industry=ctx.query_one("industry"),
            condition=ctx.query_one("condition"),
            customer_stage=ctx.query_one("customer_stage"),
            config_version=ctx.query_one("config_version"),
        )
        return ctx.service.metrics(product, scope)

    @route("POST", r"/reviews")
    def freeze_review(ctx: RequestContext) -> Any:
        body = ctx.body or {}
        review = ctx.service.freeze_review(
            product_code=body.get("product_code"),
            gate=body.get("gate"),
            scope=_scope_from(body.get("scope", {})),
        )
        return review.to_dict()

    @route("GET", r"/reviews/(?P<review_id>[^/]+)")
    def get_review(ctx: RequestContext) -> Any:
        return ctx.service.review_view(ctx.params["review_id"])

    @route("POST", r"/reviews/(?P<review_id>[^/]+)/findings")
    def add_finding(ctx: RequestContext) -> Any:
        body = ctx.body or {}
        finding = ctx.service.add_finding(
            review_id=ctx.params["review_id"],
            kind=body.get("kind"),
            text=body.get("text"),
            evidence=body.get("evidence", ()),
            segment_scope=_scope_from(body["segment_scope"]) if body.get("segment_scope") else None,
            verification_plan=body.get("verification_plan"),
        )
        return finding.to_dict()

    @route("POST", r"/reviews/(?P<review_id>[^/]+)/decision")
    def decide(ctx: RequestContext) -> Any:
        body = ctx.body or {}
        decision = ctx.service.decide(
            review_id=ctx.params["review_id"],
            action=body.get("action"),
            rationale=body.get("rationale"),
            restrictions=[_scope_from(item) for item in body.get("restrictions", ())],
            commitment_ids=body.get("commitment_ids", ()),
        )
        return decision.to_dict()

    @route("POST", r"/reviews/(?P<review_id>[^/]+)/revisions")
    def revise(ctx: RequestContext) -> Any:
        return ctx.service.revise_review(ctx.params["review_id"]).to_dict()

    @route("GET", r"/compare")
    def compare(ctx: RequestContext) -> Any:
        scope = SegmentFilter(
            industry=ctx.query_one("industry"),
            condition=ctx.query_one("condition"),
            customer_stage=ctx.query_one("customer_stage"),
            config_version=ctx.query_one("config_version"),
        )
        return ctx.service.compare(group_by=ctx.query_one("group_by"), scope=scope)

    @route("GET", r"/findings/(?P<finding_id>[^/]+)/evidence")
    def finding_evidence(ctx: RequestContext) -> Any:
        return ctx.service.finding_evidence(ctx.params["finding_id"])

    return routes


def make_handler(service: PortfolioService) -> type[BaseHTTPRequestHandler]:
    routes = build_routes(service)

    class ApiHandler(BaseHTTPRequestHandler):
        server_version = "PortfolioReview/0.1"

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            body: Any = None
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if raw:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        self._respond(400, {"error": "请求体不是合法 JSON"})
                        return
            query = parse_qs(parsed.query)
            for route_method, pattern, handler in routes:
                if route_method != method:
                    continue
                match = pattern.match(path)
                if not match:
                    continue
                ctx = RequestContext(service, match.groupdict(), query, body)
                try:
                    result = handler(ctx)
                except KeyError as exc:
                    self._respond(404, {"error": str(exc).strip("'")})
                except (ValueError, TypeError) as exc:
                    self._respond(400, {"error": str(exc)})
                else:
                    self._respond(200, result)
                return
            self._respond(404, {"error": f"路径不存在: {path}"})

        def _respond(self, status: int, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return ApiHandler


def serve(store_path: str | None, host: str, port: int) -> ThreadingHTTPServer:
    service = PortfolioService.open(store_path)
    server = ThreadingHTTPServer((host, port), make_handler(service))
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="新品市场适配复盘平台 HTTP API")
    parser.add_argument("--store", default=None, help="JSON 存储文件路径（缺省为纯内存）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    server = serve(args.store, args.host, args.port)
    print(f"API 已启动: http://{args.host}:{args.port}（存储: {args.store or '内存'}）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
