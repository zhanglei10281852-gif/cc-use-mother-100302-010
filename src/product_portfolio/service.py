"""应用服务层：平台对外的程序化 API，HTTP 接口与命令行都复用这一层。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .models import (
    ConfigVersion,
    Decision,
    DecisionAction,
    Finding,
    FindingKind,
    MarketRecord,
    Product,
    RecordKind,
    Review,
    ReviewStatus,
    SegmentFilter,
    format_instant,
    parse_instant,
    require_text,
    utcnow,
)
from .statistics import compute_metrics
from .store import JsonStore, StoredRecord

GROUP_BY_FIELDS = ("industry", "condition", "customer_stage", "config_version")


@dataclass(slots=True)
class ImportReport:
    """批量导入结果：新增、重复（幂等跳过）、拒绝（冲突或校验失败）。"""

    imported: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "summary": {
                "imported": len(self.imported),
                "duplicates": len(self.duplicates),
                "rejected": len(self.rejected),
            },
        }


def summarize_record(record: MarketRecord) -> str:
    """时间线条目的一句话摘要。"""
    details = record.details
    if record.kind is RecordKind.OPPORTUNITY:
        return f"机会登记（{record.customer_stage}）"
    if record.kind is RecordKind.FEEDBACK:
        return f"{details['channel']}反馈：{details['summary']}"
    if record.kind is RecordKind.ADOPTION:
        mark = "已验证" if details.get("verified") else "待验证"
        units = f"，{details['units']} 台套" if details.get("units") else ""
        return f"采用结果（{mark}{units}）"
    if record.kind is RecordKind.LOST_ORDER:
        return f"失单：{details['reason']}"
    if record.kind is RecordKind.FIELD_ISSUE:
        return f"现场问题[{details['severity']}/{details['status']}]：{details['summary']}"
    return f"改进承诺[{details.get('status', 'promised')}]：{details['text']}"


class PortfolioService:
    """新品市场适配复盘平台服务。"""

    def __init__(self, store: JsonStore):
        self.store = store

    @classmethod
    def open(cls, path: str | None = None, clock: Callable[[], Any] = utcnow) -> "PortfolioService":
        return cls(JsonStore(path, clock=clock))

    # -- 主数据 -----------------------------------------------------------

    def register_product(self, product_code: str, name: str, family: str = "", launched_at: str | None = None) -> dict[str, Any]:
        product = Product(product_code=product_code, name=name, family=family, launched_at=launched_at)
        return self.store.add_product(product).to_dict()

    def register_config_version(
        self, product_code: str, version: str, supersedes: str | None = None, note: str = ""
    ) -> dict[str, Any]:
        config = ConfigVersion(product_code=product_code, version=version, supersedes=supersedes, note=note)
        return self.store.add_config_version(config).to_dict()

    # -- 记录导入（幂等） ---------------------------------------------------

    def import_records(self, items: Iterable[MarketRecord | dict[str, Any]]) -> ImportReport:
        """批量导入。重复导入（同标识同内容）幂等跳过，不改变任何统计；
        同标识不同内容判定为冲突并拒绝。主数据未登记的记录同样拒绝。"""
        report = ImportReport()
        for item in items:
            try:
                record = item if isinstance(item, MarketRecord) else MarketRecord.from_dict(item)
            except (ValueError, KeyError, TypeError) as exc:
                fallback = item.get("record_id", "<unknown>") if isinstance(item, dict) else "<unknown>"
                report.rejected.append({"record_id": str(fallback), "reason": str(exc)})
                continue
            existing = self.store.records.get(record.record_id)
            if existing is not None:
                if existing.record.fingerprint() == record.fingerprint():
                    report.duplicates.append(record.record_id)
                else:
                    report.rejected.append({"record_id": record.record_id, "reason": "同标识记录内容冲突"})
                continue
            try:
                self.store.get_product(record.product_code)
                if not self.store.has_config_version(record.product_code, record.config_version):
                    raise ValueError(f"配置版本未登记: {record.product_code}/{record.config_version}")
            except (KeyError, ValueError) as exc:
                report.rejected.append({"record_id": record.record_id, "reason": str(exc)})
                continue
            self.store.add_record(record)
            report.imported.append(record.record_id)
        return report

    # -- 统一时间线 ---------------------------------------------------------

    def timeline(self, product_code: str, scope: SegmentFilter | None = None) -> list[dict[str, Any]]:
        """同一产品在某细分范围内的全部记录，按发生时间归并为一条时间线。"""
        self.store.get_product(product_code)
        entries = []
        for stored in self.store.iter_records(product_code, scope):
            record = stored.record
            entries.append(
                {
                    "record_id": record.record_id,
                    "occurred_at": record.occurred_at,
                    "kind": record.kind.value,
                    "product_code": record.product_code,
                    "industry": record.industry,
                    "condition": record.condition,
                    "customer_stage": record.customer_stage,
                    "config_version": record.config_version,
                    "source": record.source,
                    "summary": summarize_record(record),
                    "details": record.details,
                    "ingested_at": stored.ingested_at,
                }
            )
        return entries

    def metrics(self, product_code: str, scope: SegmentFilter | None = None) -> dict[str, Any]:
        self.store.get_product(product_code)
        records = [stored.record for stored in self.store.iter_records(product_code, scope)]
        return compute_metrics(records, self.store.clock())

    # -- 阶段评审 -----------------------------------------------------------

    def _freeze(self, product_code: str, gate: str, scope: SegmentFilter, revision_no: int, revision_of: str | None) -> Review:
        self.store.get_product(product_code)
        now = self.store.clock()
        sample_records = list(self.store.iter_records(product_code, scope))
        sample = tuple(
            {"record_id": stored.record.record_id, "fingerprint": stored.record.fingerprint()}
            for stored in sample_records
        )
        stats = compute_metrics([stored.record for stored in sample_records], now)
        review = Review(
            review_id=f"RV-{product_code}-{gate}-v{revision_no}",
            product_code=product_code,
            gate=gate,
            scope=scope,
            revision_no=revision_no,
            revision_of=revision_of,
            status=ReviewStatus.FROZEN,
            frozen_at=format_instant(now),
            sample=sample,
            stats=stats,
        )
        return self.store.add_review(review)

    def freeze_review(self, product_code: str, gate: str, scope: SegmentFilter | None = None) -> Review:
        """开启阶段评审：冻结当时样本与统计。同一产品同一阶段门只能开启一次，
        之后的数据变化通过修订（revise_review）产生新版本。"""
        gate = require_text(gate, "gate")
        if self.store.review_chain(product_code, gate):
            raise ValueError(f"产品 {product_code} 的阶段门 {gate} 已存在评审，请使用修订")
        return self._freeze(product_code, gate, scope or SegmentFilter(), revision_no=1, revision_of=None)

    def revise_review(self, review_id: str) -> Review:
        """基于既有评审生成修订版本：重新冻结当前样本（含迟到数据）。
        原评审的样本、统计与决策保持原样，可追溯。"""
        previous = self.store.get_review(review_id)
        chain = self.store.review_chain(previous.product_code, previous.gate)
        latest = chain[-1]
        if previous.review_id != latest.review_id:
            raise ValueError(f"只能从最新版本 {latest.review_id} 发起修订")
        return self._freeze(
            previous.product_code,
            previous.gate,
            previous.scope,
            revision_no=latest.revision_no + 1,
            revision_of=latest.review_id,
        )

    def add_finding(
        self,
        review_id: str,
        kind: str | FindingKind,
        text: str,
        evidence: Iterable[str] = (),
        segment_scope: SegmentFilter | None = None,
        verification_plan: str | None = None,
    ) -> Finding:
        """登记评审结论。事实与推断必须有冻结样本内的证据；假设必须附验证计划。
        证据记录必须落在结论声明的细分范围内，防止跨市场套用。"""
        review = self.store.get_review(review_id)
        if review.status is not ReviewStatus.FROZEN:
            raise ValueError(f"评审 {review_id} 已决策，不能再登记结论")
        kind = FindingKind(kind)
        text = require_text(text, "结论内容 text")
        scope = segment_scope or review.scope
        if not scope.compatible_with(review.scope):
            raise ValueError("结论细分范围超出评审范围")
        evidence_ids = tuple(dict.fromkeys(require_text(item, "evidence") for item in evidence))
        sample_ids = set(review.sample_ids)
        for record_id in evidence_ids:
            if record_id not in sample_ids:
                raise ValueError(f"证据 {record_id} 不在冻结样本内")
            if not scope.matches(self.store.get_record(record_id).record):
                raise ValueError(f"证据 {record_id} 不属于结论声明的细分范围")
        if kind in (FindingKind.FACT, FindingKind.INFERENCE) and not evidence_ids:
            raise ValueError("事实与推断必须至少引用一条冻结样本内的记录")
        if kind is FindingKind.HYPOTHESIS:
            verification_plan = require_text(verification_plan, "假设的验证计划 verification_plan")
        finding = Finding(
            finding_id=f"{review_id}-F{len(review.findings) + 1}",
            kind=kind,
            text=text,
            segment_scope=scope,
            evidence=evidence_ids,
            verification_plan=verification_plan,
            created_at=format_instant(self.store.clock()),
        )
        review.findings.append(finding)
        self.store.update_review(review)
        return finding

    def decide(
        self,
        review_id: str,
        action: str | DecisionAction,
        rationale: str,
        restrictions: Iterable[SegmentFilter] = (),
        commitment_ids: Iterable[str] = (),
    ) -> Decision:
        """登记阶段决策：继续推广 / 限制场景 / 安排改进 / 退出。决策后评审关闭。"""
        review = self.store.get_review(review_id)
        if review.status is not ReviewStatus.FROZEN:
            raise ValueError(f"评审 {review_id} 已完成决策")
        action = DecisionAction(action)
        rationale = require_text(rationale, "决策理由 rationale")
        restrictions = tuple(restrictions)
        commitment_ids = tuple(dict.fromkeys(commitment_ids))
        if action is DecisionAction.RESTRICT:
            if not restrictions:
                raise ValueError("限制场景决策必须给出至少一个受限细分范围")
            for scope in restrictions:
                if not scope.compatible_with(review.scope):
                    raise ValueError("受限细分范围超出评审范围")
        if action is DecisionAction.IMPROVE:
            if not commitment_ids:
                raise ValueError("安排改进决策必须引用样本内的改进承诺记录")
            sample_ids = set(review.sample_ids)
            for record_id in commitment_ids:
                if record_id not in sample_ids:
                    raise ValueError(f"改进承诺 {record_id} 不在冻结样本内")
                if self.store.get_record(record_id).record.kind is not RecordKind.IMPROVEMENT_COMMITMENT:
                    raise ValueError(f"记录 {record_id} 不是改进承诺")
        review.decision = Decision(
            action=action,
            rationale=rationale,
            decided_at=format_instant(self.store.clock()),
            restrictions=restrictions,
            commitment_ids=commitment_ids,
        )
        review.status = ReviewStatus.DECIDED
        self.store.update_review(review)
        return review.decision

    def late_records(self, review_id: str) -> list[dict[str, Any]]:
        """冻结后到达的迟到数据（只读展示，不改写冻结结果）。"""
        review = self.store.get_review(review_id)
        return [
            {
                "record_id": stored.record.record_id,
                "occurred_at": stored.record.occurred_at,
                "ingested_at": stored.ingested_at,
                "kind": stored.record.kind.value,
                "summary": summarize_record(stored.record),
            }
            for stored in self.store.late_records_for(review)
        ]

    def review_view(self, review_id: str) -> dict[str, Any]:
        review = self.store.get_review(review_id)
        view = review.to_dict()
        view["late_records"] = self.late_records(review_id)
        view["revision_chain"] = [item.review_id for item in self.store.review_chain(review.product_code, review.gate)]
        return view

    # -- 管理层对比与追溯 -----------------------------------------------------

    def compare(self, group_by: str | None = None, scope: SegmentFilter | None = None) -> dict[str, Any]:
        """按细分维度对比各新品的真实适配度。每行指标都带 evidence 记录清单。"""
        if group_by is not None and group_by not in GROUP_BY_FIELDS:
            raise ValueError(f"group_by 必须是 {GROUP_BY_FIELDS} 之一")
        now = self.store.clock()
        rows = []
        for product_code in sorted(self.store.products):
            self.store.get_product(product_code)
            grouped: dict[str | None, list[MarketRecord]] = {}
            for stored in self.store.iter_records(product_code, scope):
                key = getattr(stored.record, group_by) if group_by else None
                grouped.setdefault(key, []).append(stored.record)
            if not grouped:
                grouped[None] = []
            for key in sorted(grouped, key=lambda item: (item is None, item)):
                metrics = compute_metrics(grouped[key], now)
                rows.append(
                    {
                        "product_code": product_code,
                        "group_by": group_by,
                        "group": key,
                        "metrics": metrics,
                        "fit_score": metrics["fit_score"],
                    }
                )
        return {"generated_at": format_instant(now), "group_by": group_by, "rows": rows}

    def finding_evidence(self, finding_id: str) -> dict[str, Any]:
        """追溯某项结论采用了哪些记录，并校验记录指纹与冻结样本一致。"""
        review, finding = self.store.find_finding(finding_id)
        sample_fingerprints = {entry["record_id"]: entry["fingerprint"] for entry in review.sample}
        records = []
        for record_id in finding.evidence:
            stored = self.store.get_record(record_id)
            records.append(
                {
                    **stored.record.to_dict(),
                    "ingested_at": stored.ingested_at,
                    "fingerprint_match": sample_fingerprints.get(record_id) == stored.record.fingerprint(),
                }
            )
        return {
            "finding": finding.to_dict(),
            "review_id": review.review_id,
            "frozen_at": review.frozen_at,
            "records": records,
        }
