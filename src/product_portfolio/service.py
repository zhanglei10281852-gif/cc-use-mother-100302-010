"""应用服务：新品市场适配复盘平台的 Python API。

承载全部业务规则：

- 统一口径登记机会与反馈（行业 / 工况 / 客户阶段 / 配置版本）
- 幂等导入：重复导入不改变统计，同标识不同内容视为冲突
- 阶段评审冻结样本，结论区分事实 / 推断 / 待验证假设
- 迟到数据只能触发修订，已冻结修订不可改写
- 配置升级后旧反馈保留在原版本
- 适配度对比与结论追溯
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .analytics import compare_segments, compute_metrics
from .models import (
    Conclusion,
    ConfigVersion,
    Decision,
    DecisionKind,
    EvidenceClass,
    FeedbackKind,
    FeedbackRecord,
    Opportunity,
    Product,
    Review,
    ReviewRevision,
    Scope,
    config_key,
    dimension_key,
    sort_records,
)
from .store import InMemoryStore


class ValidationError(ValueError):
    """输入或口径校验失败。"""


class ConflictError(ValueError):
    """同一业务标识出现不同内容。"""


class StateError(ValueError):
    """状态流转非法（如对已决策修订追加结论）。"""


class NotFoundError(KeyError):
    """引用的对象不存在。"""


@dataclass(frozen=True, slots=True)
class ImportResult:
    """一次导入的结果：新增 / 重复（幂等跳过）/ 冲突（拒绝入库）。"""

    added: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    conflicts: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added),
            "duplicates": list(self.duplicates),
            "conflicts": [dict(c) for c in self.conflicts],
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioService:
    """平台门面：所有用例都通过该服务完成。"""

    def __init__(
        self,
        store: Any | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store if store is not None else InMemoryStore()
        self._clock = clock
        self._state = self._store.load()

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    def _now(self) -> str:
        return self._clock().isoformat()

    def _persist(self) -> None:
        self._store.save(self._state)

    # ------------------------------------------------------------------
    # 产品目录与配置版本
    # ------------------------------------------------------------------

    def add_product(self, product_code: str, name: str, launched_at: str) -> Product:
        product = Product(product_code=product_code, name=name, launched_at=launched_at)
        key = dimension_key(product.product_code)
        if key in self._state["products"]:
            raise ConflictError(f"产品 {product.product_code} 已存在")
        self._state["products"][key] = product.to_dict()
        self._persist()
        return product

    def list_products(self) -> list[Product]:
        rows = [Product.from_dict(d) for d in self._state["products"].values()]
        return sorted(rows, key=lambda p: p.product_code)

    def get_product(self, product_code: str) -> Product:
        key = dimension_key(product_code)
        data = self._state["products"].get(key)
        if data is None:
            raise NotFoundError(f"产品不存在: {product_code}")
        return Product.from_dict(data)

    def add_config_version(
        self,
        product_code: str,
        version: str,
        released_at: str,
        *,
        supersedes: str | None = None,
        note: str = "",
    ) -> ConfigVersion:
        product = self.get_product(product_code)
        config = ConfigVersion(
            product_code=product.product_code,
            version=version,
            released_at=released_at,
            supersedes=supersedes,
            note=note,
        )
        if config.key in self._state["config_versions"]:
            raise ConflictError(f"配置版本已存在: {product.product_code}@{config.version}")
        if config.supersedes is not None:
            parent_key = config_key(product.product_code, config.supersedes)
            if parent_key not in self._state["config_versions"]:
                raise NotFoundError(f"被替代的配置版本不存在: {config.supersedes}")
        self._state["config_versions"][config.key] = config.to_dict()
        self._persist()
        return config

    def list_config_versions(self, product_code: str) -> list[ConfigVersion]:
        product = self.get_product(product_code)
        prefix = f"{dimension_key(product.product_code)}@"
        rows = [
            ConfigVersion.from_dict(d)
            for key, d in self._state["config_versions"].items()
            if key.startswith(prefix)
        ]
        return sorted(rows, key=lambda c: (c.released_at, c.version))

    def _require_config(self, product_code: str, version: str) -> None:
        if config_key(product_code, version) not in self._state["config_versions"]:
            raise NotFoundError(f"产品 {product_code} 未登记配置版本: {version}")

    # ------------------------------------------------------------------
    # 机会与反馈的幂等导入
    # ------------------------------------------------------------------

    def import_opportunities(self, items: Iterable[Mapping[str, Any]]) -> ImportResult:
        added: list[str] = []
        duplicates: list[str] = []
        conflicts: list[dict[str, str]] = []
        for raw in items:
            try:
                opportunity = Opportunity.from_dict({**raw, "registered_at": self._now()})
                self.get_product(opportunity.product_code)
                self._require_config(opportunity.product_code, opportunity.config_version)
            except (ValueError, NotFoundError, TypeError) as exc:
                conflicts.append({"opportunity_code": str(raw.get("opportunity_code", "")), "reason": str(exc)})
                continue
            key = dimension_key(opportunity.opportunity_code)
            existing = self._state["opportunities"].get(key)
            if existing is not None:
                if Opportunity.from_dict(existing).fingerprint() == opportunity.fingerprint():
                    duplicates.append(opportunity.opportunity_code)
                else:
                    conflicts.append(
                        {
                            "opportunity_code": opportunity.opportunity_code,
                            "reason": "同一机会编码出现不同内容",
                        }
                    )
                continue
            self._state["opportunities"][key] = opportunity.to_dict()
            added.append(opportunity.opportunity_code)
        if added:
            self._persist()
        return ImportResult(added=tuple(added), duplicates=tuple(duplicates), conflicts=tuple(conflicts))

    def import_records(self, items: Iterable[Mapping[str, Any]]) -> ImportResult:
        """幂等导入反馈记录：内容一致的重复导入被跳过，统计不变。"""
        added: list[str] = []
        duplicates: list[str] = []
        conflicts: list[dict[str, str]] = []
        for raw in items:
            try:
                record = FeedbackRecord.from_dict({**raw, "received_at": self._now()})
                self.get_product(record.product_code)
                self._require_config(record.product_code, record.config_version)
                if record.opportunity_code is not None and (
                    dimension_key(record.opportunity_code) not in self._state["opportunities"]
                ):
                    raise NotFoundError(f"机会不存在: {record.opportunity_code}")
            except (ValueError, NotFoundError, TypeError) as exc:
                conflicts.append({"record_id": str(raw.get("record_id", "")), "reason": str(exc)})
                continue
            key = dimension_key(record.record_id)
            existing = self._state["records"].get(key)
            if existing is not None:
                if FeedbackRecord.from_dict(existing).fingerprint() == record.fingerprint():
                    duplicates.append(record.record_id)
                else:
                    conflicts.append(
                        {"record_id": record.record_id, "reason": "同一记录标识出现不同内容"}
                    )
                continue
            self._state["records"][key] = record.to_dict()
            added.append(record.record_id)
        if added:
            self._persist()
        return ImportResult(added=tuple(added), duplicates=tuple(duplicates), conflicts=tuple(conflicts))

    def get_record(self, record_id: str) -> FeedbackRecord:
        data = self._state["records"].get(dimension_key(record_id))
        if data is None:
            raise NotFoundError(f"记录不存在: {record_id}")
        return FeedbackRecord.from_dict(data)

    # ------------------------------------------------------------------
    # 统一时间线
    # ------------------------------------------------------------------

    def timeline(
        self,
        product_code: str,
        *,
        industry: str | None = None,
        conditions: str | None = None,
        customer_stage: str | None = None,
        config_version: str | None = None,
    ) -> list[FeedbackRecord]:
        """四类反馈归并到同一时间线，按发生时间排序。"""
        product = self.get_product(product_code)
        scope = Scope(
            industry=industry,
            conditions=conditions,
            customer_stage=customer_stage,
            config_version=config_version,
        )
        matched = [
            FeedbackRecord.from_dict(d)
            for d in self._state["records"].values()
            if scope.accepts(FeedbackRecord.from_dict(d), product_code=product.product_code)
        ]
        return sort_records(matched)

    # ------------------------------------------------------------------
    # 阶段评审：冻结样本、结论、决策、修订
    # ------------------------------------------------------------------

    def _matching_records(self, product_code: str, scope: Scope) -> list[FeedbackRecord]:
        return sort_records(
            FeedbackRecord.from_dict(d)
            for d in self._state["records"].values()
            if scope.accepts(FeedbackRecord.from_dict(d), product_code=product_code)
        )

    @staticmethod
    def _freeze(records: list[FeedbackRecord]) -> tuple[tuple[str, str], ...]:
        return tuple((r.record_id, r.fingerprint()) for r in sorted(records, key=lambda r: r.record_id))

    def create_review(
        self,
        product_code: str,
        *,
        industry: str | None = None,
        conditions: str | None = None,
        customer_stage: str | None = None,
        config_version: str | None = None,
    ) -> Review:
        """创建阶段评审并立即冻结当前样本（第 1 修订）。"""
        product = self.get_product(product_code)
        scope = Scope(
            industry=industry,
            conditions=conditions,
            customer_stage=customer_stage,
            config_version=config_version,
        )
        records = self._matching_records(product.product_code, scope)
        self._state["counters"]["review"] += 1
        review = Review(
            review_id=f"RV-{self._state['counters']['review']:04d}",
            product_code=product.product_code,
            scope=scope,
            created_at=self._now(),
            revisions=(
                ReviewRevision(
                    revision_no=1,
                    frozen_at=self._now(),
                    sample=self._freeze(records),
                    stats=compute_metrics(records),
                ),
            ),
        )
        self._state["reviews"][dimension_key(review.review_id)] = review.to_dict()
        self._persist()
        return review

    def get_review(self, review_id: str) -> Review:
        data = self._state["reviews"].get(dimension_key(review_id))
        if data is None:
            raise NotFoundError(f"评审不存在: {review_id}")
        return Review.from_dict(data)

    def list_reviews(self, product_code: str | None = None) -> list[Review]:
        rows = [Review.from_dict(d) for d in self._state["reviews"].values()]
        if product_code is not None:
            key = dimension_key(product_code)
            rows = [r for r in rows if dimension_key(r.product_code) == key]
        return sorted(rows, key=lambda r: r.review_id)

    def _editable_review(self, review_id: str) -> Review:
        review = self.get_review(review_id)
        if review.status != "open":
            raise StateError(f"评审 {review.review_id} 已关闭")
        if review.latest.decision is not None:
            raise StateError(
                f"评审 {review.review_id} 第 {review.latest.revision_no} 修订已决策，"
                "如需调整请等待迟到数据触发新修订"
            )
        return review

    @staticmethod
    def _resolve_sample_ids(
        review: Review, cited: Iterable[str]
    ) -> tuple[tuple[str, ...], list[str]]:
        """把引用的记录标识解析为冻结样本内的规范标识（大小写不敏感）。"""
        index = {dimension_key(rid): rid for rid in review.latest.sample_ids}
        resolved: list[str] = []
        missing: list[str] = []
        for raw in cited:
            key = dimension_key(str(raw))
            if key in index:
                resolved.append(index[key])
            else:
                missing.append(str(raw))
        return tuple(dict.fromkeys(resolved)), missing

    def add_conclusion(
        self,
        review_id: str,
        statement: str,
        evidence_class: str,
        record_ids: Iterable[str] = (),
    ) -> Review:
        """追加结论；事实与推断必须引用冻结样本内的记录，假设不得引用记录。"""
        review = self._editable_review(review_id)
        resolved, missing = self._resolve_sample_ids(review, record_ids)
        conclusion = Conclusion(
            statement=statement, evidence_class=evidence_class, record_ids=resolved
        )
        if conclusion.evidence_class == EvidenceClass.HYPOTHESIS.value:
            if conclusion.record_ids or missing:
                raise ValidationError("待验证假设不得引用记录，验证后才能升级为事实或推断")
        else:
            if not conclusion.record_ids:
                raise ValidationError("事实与推断必须引用至少一条冻结样本内的记录")
            if missing:
                raise ValidationError(
                    "结论引用了冻结样本之外的记录（可能存在跨细分市场套用）: "
                    + ", ".join(sorted(missing))
                )
        revision = ReviewRevision(
            revision_no=review.latest.revision_no,
            frozen_at=review.latest.frozen_at,
            sample=review.latest.sample,
            stats=review.latest.stats,
            reason=review.latest.reason,
            conclusions=review.latest.conclusions + (conclusion,),
            decision=review.latest.decision,
        )
        updated = review.with_updated_latest(revision)
        self._state["reviews"][dimension_key(review.review_id)] = updated.to_dict()
        self._persist()
        return updated

    def decide(
        self,
        review_id: str,
        kind: str,
        rationale: str,
        *,
        allowed_scope: Mapping[str, Any] | None = None,
        improvement_record_ids: Iterable[str] = (),
    ) -> Review:
        """对当前修订作出决策：继续推广 / 限制场景 / 安排改进 / 退出。"""
        review = self._editable_review(review_id)
        if not review.latest.conclusions:
            raise StateError("必须先记录结论（事实 / 推断 / 假设）再决策")
        resolved_improvements, missing_improvements = self._resolve_sample_ids(
            review, improvement_record_ids
        )
        decision = Decision(
            kind=kind,
            rationale=rationale,
            decided_at=self._now(),
            allowed_scope=dict(allowed_scope) if allowed_scope is not None else None,
            improvement_record_ids=resolved_improvements,
        )
        if decision.kind == DecisionKind.RESTRICT.value and not decision.allowed_scope:
            raise ValidationError("限制场景决策必须给出允许继续推广的范围 allowed_scope")
        if decision.kind == DecisionKind.IMPROVE.value:
            if not decision.improvement_record_ids:
                raise ValidationError("安排改进决策必须引用样本内的改进承诺记录")
            if missing_improvements:
                raise ValidationError(
                    "改进决策引用了冻结样本之外的记录: " + ", ".join(sorted(missing_improvements))
                )
            for record_id in decision.improvement_record_ids:
                record = self.get_record(record_id)
                if record.kind != FeedbackKind.IMPROVEMENT.value:
                    raise ValidationError(f"记录 {record_id} 不是改进承诺")
        revision = ReviewRevision(
            revision_no=review.latest.revision_no,
            frozen_at=review.latest.frozen_at,
            sample=review.latest.sample,
            stats=review.latest.stats,
            reason=review.latest.reason,
            conclusions=review.latest.conclusions,
            decision=decision,
        )
        updated = review.with_updated_latest(revision)
        self._state["reviews"][dimension_key(review.review_id)] = updated.to_dict()
        self._persist()
        return updated

    def close_review(self, review_id: str) -> Review:
        review = self.get_review(review_id)
        if review.latest.decision is None:
            raise StateError("尚未决策的评审不能关闭")
        updated = replace(review, status="closed")
        self._state["reviews"][dimension_key(review.review_id)] = updated.to_dict()
        self._persist()
        return updated

    # ------------------------------------------------------------------
    # 迟到数据与修订
    # ------------------------------------------------------------------

    def late_records(self, review_id: str) -> list[FeedbackRecord]:
        """评审冻结后到达且属于评审范围的记录（即迟到数据）。"""
        review = self.get_review(review_id)
        known = {record_id for revision in review.revisions for record_id in revision.sample_ids}
        current = self._matching_records(review.product_code, review.scope)
        return [r for r in current if r.record_id not in known]

    def revise_review(self, review_id: str, reason: str) -> Review:
        """迟到数据触发的修订：重新冻结样本，历史修订保持不变。"""
        review = self.get_review(review_id)
        if review.status != "open":
            raise StateError(f"评审 {review.review_id} 已关闭，不能修订")
        late = self.late_records(review_id)
        if not late:
            raise StateError("没有迟到数据，无需修订")
        if not reason or not reason.strip():
            raise ValidationError("修订必须说明原因")
        records = self._matching_records(review.product_code, review.scope)
        revision = ReviewRevision(
            revision_no=review.latest.revision_no + 1,
            frozen_at=self._now(),
            sample=self._freeze(records),
            stats=compute_metrics(records),
            reason=reason.strip(),
        )
        updated = review.with_revision(revision)
        self._state["reviews"][dimension_key(review.review_id)] = updated.to_dict()
        self._persist()
        return updated

    # ------------------------------------------------------------------
    # 适配度对比与追溯
    # ------------------------------------------------------------------

    def compare(
        self,
        *,
        product_codes: Iterable[str] | None = None,
        industry: str | None = None,
        conditions: str | None = None,
        customer_stage: str | None = None,
        config_version: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 产品 × 细分市场 对比真实适配度（基于当前全部记录）。"""
        records = [FeedbackRecord.from_dict(d) for d in self._state["records"].values()]
        return compare_segments(
            records,
            product_codes=product_codes,
            industry=industry,
            conditions=conditions,
            customer_stage=customer_stage,
            config_version=config_version,
        )

    def trace(
        self,
        product_code: str,
        *,
        industry: str,
        conditions: str | None = None,
        customer_stage: str | None = None,
        config_version: str | None = None,
    ) -> dict[str, Any]:
        """给出某产品在某细分市场的指标及背后全部记录内容。"""
        product = self.get_product(product_code)
        scope = Scope(
            industry=industry,
            conditions=conditions,
            customer_stage=customer_stage,
            config_version=config_version,
        )
        records = self._matching_records(product.product_code, scope)
        metrics = compute_metrics(records)
        by_id = {r.record_id: r for r in records}
        return {
            "product_code": product.product_code,
            "scope": scope.to_dict(),
            "metrics": metrics,
            "records": {
                metric: [by_id[record_id].to_dict() for record_id in ids]
                for metric, ids in metrics["evidence"].items()
            },
        }

    def trace_review(self, review_id: str) -> dict[str, Any]:
        """追溯评审每项结论采用的记录，并校验冻结样本至今未被篡改。"""
        review = self.get_review(review_id)
        revisions: list[dict[str, Any]] = []
        for revision in review.revisions:
            integrity: dict[str, bool] = {}
            for record_id, fingerprint in revision.sample:
                data = self._state["records"].get(dimension_key(record_id))
                integrity[record_id] = bool(
                    data is not None and FeedbackRecord.from_dict(data).fingerprint() == fingerprint
                )
            conclusions = []
            for conclusion in revision.conclusions:
                conclusions.append(
                    {
                        **conclusion.to_dict(),
                        "records": [self.get_record(rid).to_dict() for rid in conclusion.record_ids],
                    }
                )
            revisions.append(
                {
                    "revision_no": revision.revision_no,
                    "frozen_at": revision.frozen_at,
                    "reason": revision.reason,
                    "stats": revision.stats,
                    "sample_integrity": integrity,
                    "conclusions": conclusions,
                    "decision": revision.decision.to_dict() if revision.decision else None,
                }
            )
        return {
            "review_id": review.review_id,
            "product_code": review.product_code,
            "scope": review.scope.to_dict(),
            "status": review.status,
            "revisions": revisions,
        }
