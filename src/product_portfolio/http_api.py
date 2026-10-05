"""HTTP JSON API（仅标准库实现）：管理层可通过 API 对比适配度并追溯结论。"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .service import (
    ConflictError,
    NotFoundError,
    PortfolioService,
    StateError,
    ValidationError,
)


def _first(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    return values[0] if values else None


def _csv(params: dict[str, list[str]], name: str) -> list[str] | None:
    raw = _first(params, name)
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()] or None


def make_handler(service: PortfolioService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PortfolioReview/0.1"

        # -- 基础工具 ---------------------------------------------------

        def _send(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _ok(self, data: Any, status: int = 200) -> None:
            self._send(status, {"ok": True, "data": data})

        def _fail(self, status: int, message: str) -> None:
            self._send(status, {"ok": False, "error": message})

        def _body(self) -> dict[str, Any]:
            cached = getattr(self, "_cached_body", None)
            if cached is not None:
                return cached
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                self._cached_body = {}
                return self._cached_body
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValidationError(f"请求体不是合法 JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise ValidationError("请求体必须是 JSON 对象")
            self._cached_body = data
            return data

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 保持静默便于测试
            return

        # -- 路由 ---------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            params = parse_qs(parsed.query)
            try:
                handler = self._routes().get((method, path))
                if handler is not None:
                    self._ok(handler(params))
                    return
                # 带路径参数的路由人工匹配
                if method == "GET" and path.startswith("/reviews/"):
                    review_id = path.split("/", 2)[2]
                    self._ok(service.get_review(review_id).to_dict())
                    return
                if method == "POST" and path.startswith("/reviews/"):
                    review_id, _, action = path.split("/", 2)[2].partition("/")
                    self._ok(self._review_action(review_id, action))
                    return
                self._fail(404, f"未找到路由: {method} {path}")
            except NotFoundError as exc:
                self._fail(404, str(exc.args[0] if exc.args else exc))
            except (ValidationError, StateError) as exc:
                self._fail(400, str(exc))
            except ConflictError as exc:
                self._fail(409, str(exc))

        def _routes(self) -> dict[tuple[str, str], Callable[[dict[str, list[str]]], Any]]:
            return {
                ("GET", "/health"): lambda p: {"status": "up"},
                ("GET", "/products"): lambda p: [x.to_dict() for x in service.list_products()],
                ("POST", "/products"): lambda p: service.add_product(
                    self._require("product_code"), self._require("name"), self._require("launched_at")
                ).to_dict(),
                ("POST", "/config-versions"): lambda p: service.add_config_version(
                    self._require("product_code"),
                    self._require("version"),
                    self._require("released_at"),
                    supersedes=self._body().get("supersedes"),
                    note=self._body().get("note", ""),
                ).to_dict(),
                ("POST", "/imports"): lambda p: {
                    "opportunities": service.import_opportunities(
                        self._body().get("opportunities", [])
                    ).to_dict(),
                    "records": service.import_records(self._body().get("records", [])).to_dict(),
                },
                ("GET", "/timeline"): lambda p: [
                    r.to_dict()
                    for r in service.timeline(
                        _first(p, "product") or self._missing("product"),
                        industry=_first(p, "industry"),
                        conditions=_first(p, "conditions"),
                        customer_stage=_first(p, "customer_stage"),
                        config_version=_first(p, "config_version"),
                    )
                ],
                ("POST", "/reviews"): lambda p: service.create_review(
                    self._require("product_code"),
                    industry=self._body().get("industry"),
                    conditions=self._body().get("conditions"),
                    customer_stage=self._body().get("customer_stage"),
                    config_version=self._body().get("config_version"),
                ).to_dict(),
                ("GET", "/reviews"): lambda p: [
                    r.to_dict() for r in service.list_reviews(_first(p, "product"))
                ],
                ("GET", "/compare"): lambda p: service.compare(
                    product_codes=_csv(p, "products"),
                    industry=_first(p, "industry"),
                    conditions=_first(p, "conditions"),
                    customer_stage=_first(p, "customer_stage"),
                    config_version=_first(p, "config_version"),
                ),
                ("GET", "/trace"): lambda p: service.trace(
                    _first(p, "product") or self._missing("product"),
                    industry=_first(p, "industry") or self._missing("industry"),
                    conditions=_first(p, "conditions"),
                    customer_stage=_first(p, "customer_stage"),
                    config_version=_first(p, "config_version"),
                ),
                ("GET", "/trace-review"): lambda p: service.trace_review(
                    _first(p, "review") or self._missing("review")
                ),
            }

        def _require(self, name: str) -> str:
            value = self._body().get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValidationError(f"缺少必填字段: {name}")
            return str(value)

        def _missing(self, name: str) -> str:
            raise ValidationError(f"缺少必填参数: {name}")

        def _review_action(self, review_id: str, action: str) -> Any:
            body = self._body()
            if action == "conclusions":
                return service.add_conclusion(
                    review_id,
                    str(body.get("statement", "")),
                    str(body.get("evidence_class", "")),
                    body.get("record_ids", []),
                ).to_dict()
            if action == "decision":
                return service.decide(
                    review_id,
                    str(body.get("kind", "")),
                    str(body.get("rationale", "")),
                    allowed_scope=body.get("allowed_scope"),
                    improvement_record_ids=body.get("improvement_record_ids", []),
                ).to_dict()
            if action == "revisions":
                return service.revise_review(review_id, str(body.get("reason", ""))).to_dict()
            if action == "close":
                return service.close_review(review_id).to_dict()
            raise NotFoundError(f"未知的评审操作: {action}")

    return Handler


def serve(service: PortfolioService, *, host: str = "127.0.0.1", port: int = 8000) -> None:
    """启动 HTTP 服务（阻塞）。"""
    server = ThreadingHTTPServer((host, port), make_handler(service))
    try:
        server.serve_forever()
    finally:
        server.server_close()
