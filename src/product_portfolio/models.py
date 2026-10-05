"""新品市场适配复盘的领域模型。

统一各部门统计口径：所有机会与反馈都按 行业 / 工况 / 客户阶段 / 配置版本
四个维度登记，文本维度做规范化处理，枚举维度做严格校验，避免同一细分
市场被拆成多种写法。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def normalize_text(value: str, *, field_name: str = "文本") -> str:
    """规范化文本维度：去首尾空白并压缩内部空白，保证各部门口径一致。"""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


def dimension_key(value: str) -> str:
    """维度匹配键：规范化并忽略大小写，用于跨部门口径对齐。"""
    return normalize_text(value).casefold()


def require_iso8601(value: str, *, field_name: str) -> str:
    """校验 ISO-8601 时间字符串，返回原值。"""
    normalized = normalize_text(value, field_name=field_name)
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} 不是合法的 ISO-8601 时间: {value!r}") from exc
    return normalized


def stable_fingerprint(payload: Mapping[str, Any]) -> str:
    """对任意可 JSON 序列化的内容生成稳定摘要。"""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 受控词表（统一口径）
# ---------------------------------------------------------------------------


class _ParsableEnum(str, Enum):
    @classmethod
    def parse(cls, value: str, *, field_name: str) -> "_ParsableEnum":
        normalized = normalize_text(value, field_name=field_name).lower()
        for member in cls:  # type: ignore[attr-defined]
            if member.value == normalized:
                return member
        options = ", ".join(m.value for m in cls)  # type: ignore[attr-defined]
        raise ValueError(f"{field_name} 取值 {value!r} 非法，可选: {options}")


class CustomerStage(_ParsableEnum):
    """客户阶段。"""

    TRIAL = "trial"  # 试装
    QUOTE = "quote"  # 报价
    PILOT = "pilot"  # 小批量
    VOLUME = "volume"  # 批量


class FeedbackKind(_ParsableEnum):
    """反馈记录类型，四类记录归并到同一时间线。"""

    ADOPTION = "adoption"  # 可验证的采用结果
    LOST_ORDER = "lost_order"  # 失单原因
    FIELD_ISSUE = "field_issue"  # 现场问题
    IMPROVEMENT = "improvement_commitment"  # 改进承诺


class Severity(_ParsableEnum):
    """现场问题严重度。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_WEIGHT: dict[str, int] = {
    Severity.LOW.value: 1,
    Severity.MEDIUM.value: 2,
    Severity.HIGH.value: 4,
    Severity.CRITICAL.value: 8,
}


class CommitmentStatus(_ParsableEnum):
    """改进承诺状态。"""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class EvidenceClass(_ParsableEnum):
    """评审结论的证据等级：事实 / 推断 / 待验证假设。"""

    FACT = "fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"


class DecisionKind(_ParsableEnum):
    """阶段评审决策。"""

    CONTINUE = "continue"  # 继续推广
    RESTRICT = "restrict"  # 限制场景
    IMPROVE = "improve"  # 安排改进
    EXIT = "exit"  # 退出


# ---------------------------------------------------------------------------
# 注册类对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Product:
    """一款新品。"""

    product_code: str
    name: str
    launched_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_code", normalize_text(self.product_code, field_name="产品编码"))
        object.__setattr__(self, "name", normalize_text(self.name, field_name="产品名称"))
        object.__setattr__(self, "launched_at", require_iso8601(self.launched_at, field_name="上市时间"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Product":
        return cls(**dict(data))


@dataclass(frozen=True, slots=True)
class ConfigVersion:
    """产品的一个配置版本；升级产生新版本，旧反馈保留在原版本。"""

    product_code: str
    version: str
    released_at: str
    supersedes: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_code", normalize_text(self.product_code, field_name="产品编码"))
        object.__setattr__(self, "version", normalize_text(self.version, field_name="配置版本"))
        object.__setattr__(self, "released_at", require_iso8601(self.released_at, field_name="版本发布时间"))
        if self.supersedes is not None:
            object.__setattr__(self, "supersedes", normalize_text(self.supersedes, field_name="被替代版本"))

    @property
    def key(self) -> str:
        return config_key(self.product_code, self.version)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConfigVersion":
        return cls(**dict(data))


def config_key(product_code: str, version: str) -> str:
    return f"{dimension_key(product_code)}@{dimension_key(version)}"


@dataclass(frozen=True, slots=True)
class Opportunity:
    """登记的销售机会；结果（采用/失单）由反馈记录承载，机会本身不可变。"""

    opportunity_code: str
    product_code: str
    industry: str
    conditions: str
    customer_stage: str
    config_version: str
    source: str
    registered_at: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "opportunity_code", normalize_text(self.opportunity_code, field_name="机会编码"))
        object.__setattr__(self, "product_code", normalize_text(self.product_code, field_name="产品编码"))
        object.__setattr__(self, "industry", normalize_text(self.industry, field_name="行业"))
        object.__setattr__(self, "conditions", normalize_text(self.conditions, field_name="工况"))
        object.__setattr__(
            self,
            "customer_stage",
            CustomerStage.parse(self.customer_stage, field_name="客户阶段").value,
        )
        object.__setattr__(self, "config_version", normalize_text(self.config_version, field_name="配置版本"))
        object.__setattr__(self, "source", normalize_text(self.source, field_name="来源部门"))
        if self.registered_at:
            object.__setattr__(self, "registered_at", require_iso8601(self.registered_at, field_name="登记时间"))

    def fingerprint(self) -> str:
        """业务内容摘要（不含登记时间等导入元数据），用于幂等去重。"""
        payload = self.to_dict()
        payload.pop("registered_at", None)
        return stable_fingerprint(payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Opportunity":
        return cls(**dict(data))


# ---------------------------------------------------------------------------
# 反馈记录（统一时间线的条目）
# ---------------------------------------------------------------------------


def _validate_details(kind: str, details: Mapping[str, Any]) -> dict[str, Any]:
    """按记录类型校验必填字段，保证关键口径（如采用结果必须可验证）。"""
    data = dict(details)
    if kind == FeedbackKind.ADOPTION.value:
        data["verification_ref"] = normalize_text(
            str(data.get("verification_ref", "")), field_name="采用结果验证依据"
        )
    elif kind == FeedbackKind.LOST_ORDER.value:
        data["reason"] = normalize_text(str(data.get("reason", "")), field_name="失单原因")
    elif kind == FeedbackKind.FIELD_ISSUE.value:
        data["severity"] = Severity.parse(str(data.get("severity", "")), field_name="严重度").value
        data["resolved"] = bool(data.get("resolved", False))
    elif kind == FeedbackKind.IMPROVEMENT.value:
        data["owner"] = normalize_text(str(data.get("owner", "")), field_name="改进负责人")
        data["status"] = CommitmentStatus.parse(
            str(data.get("status", CommitmentStatus.OPEN.value)), field_name="承诺状态"
        ).value
        if data.get("due_date"):
            data["due_date"] = require_iso8601(str(data["due_date"]), field_name="承诺期限")
    return data


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    """一条市场反馈：采用结果 / 失单原因 / 现场问题 / 改进承诺。"""

    record_id: str
    product_code: str
    industry: str
    conditions: str
    customer_stage: str
    config_version: str
    kind: str
    occurred_at: str
    source: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    opportunity_code: str | None = None
    received_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", normalize_text(self.record_id, field_name="记录标识"))
        object.__setattr__(self, "product_code", normalize_text(self.product_code, field_name="产品编码"))
        object.__setattr__(self, "industry", normalize_text(self.industry, field_name="行业"))
        object.__setattr__(self, "conditions", normalize_text(self.conditions, field_name="工况"))
        object.__setattr__(
            self,
            "customer_stage",
            CustomerStage.parse(self.customer_stage, field_name="客户阶段").value,
        )
        object.__setattr__(self, "config_version", normalize_text(self.config_version, field_name="配置版本"))
        kind = FeedbackKind.parse(self.kind, field_name="记录类型").value
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "occurred_at", require_iso8601(self.occurred_at, field_name="发生时间"))
        object.__setattr__(self, "source", normalize_text(self.source, field_name="来源部门"))
        object.__setattr__(self, "summary", normalize_text(self.summary, field_name="摘要"))
        object.__setattr__(self, "details", _validate_details(kind, self.details))
        if self.opportunity_code is not None:
            object.__setattr__(
                self, "opportunity_code", normalize_text(self.opportunity_code, field_name="机会编码")
            )
        if self.received_at:
            object.__setattr__(self, "received_at", require_iso8601(self.received_at, field_name="到达时间"))

    def fingerprint(self) -> str:
        """业务内容摘要（不含 received_at），重复导入据此判等。"""
        payload = self.to_dict()
        payload.pop("received_at", None)
        return stable_fingerprint(payload)

    def matches(
        self,
        *,
        product_code: str | None = None,
        industry: str | None = None,
        conditions: str | None = None,
        customer_stage: str | None = None,
        config_version: str | None = None,
    ) -> bool:
        """按维度键匹配，保证筛选口径与登记口径一致。"""
        checks = (
            (product_code, self.product_code),
            (industry, self.industry),
            (conditions, self.conditions),
            (customer_stage, self.customer_stage),
            (config_version, self.config_version),
        )
        for expected, actual in checks:
            if expected is not None and dimension_key(expected) != dimension_key(actual):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeedbackRecord":
        return cls(**dict(data))


# ---------------------------------------------------------------------------
# 阶段评审（冻结样本 + 结论 + 决策 + 修订）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scope:
    """评审或统计的范围；空维度表示不限。"""

    industry: str | None = None
    conditions: str | None = None
    customer_stage: str | None = None
    config_version: str | None = None

    def __post_init__(self) -> None:
        for name in ("industry", "conditions", "config_version"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, normalize_text(value, field_name=name))
        if self.customer_stage is not None:
            object.__setattr__(
                self,
                "customer_stage",
                CustomerStage.parse(self.customer_stage, field_name="客户阶段").value,
            )

    def accepts(self, record: FeedbackRecord, *, product_code: str) -> bool:
        return record.matches(
            product_code=product_code,
            industry=self.industry,
            conditions=self.conditions,
            customer_stage=self.customer_stage,
            config_version=self.config_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Scope":
        return cls(**dict(data or {}))


@dataclass(frozen=True, slots=True)
class Conclusion:
    """一条评审结论，必须区分事实 / 推断 / 待验证假设。"""

    statement: str
    evidence_class: str
    record_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement", normalize_text(self.statement, field_name="结论陈述"))
        object.__setattr__(
            self,
            "evidence_class",
            EvidenceClass.parse(self.evidence_class, field_name="证据等级").value,
        )
        object.__setattr__(self, "record_ids", tuple(dict.fromkeys(self.record_ids)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "evidence_class": self.evidence_class,
            "record_ids": list(self.record_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Conclusion":
        return cls(
            statement=data["statement"],
            evidence_class=data["evidence_class"],
            record_ids=tuple(data.get("record_ids", ())),
        )


@dataclass(frozen=True, slots=True)
class Decision:
    """评审决策：继续推广 / 限制场景 / 安排改进 / 退出。"""

    kind: str
    rationale: str
    decided_at: str
    allowed_scope: dict[str, Any] | None = None
    improvement_record_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", DecisionKind.parse(self.kind, field_name="决策类型").value)
        object.__setattr__(self, "rationale", normalize_text(self.rationale, field_name="决策理由"))
        object.__setattr__(self, "decided_at", require_iso8601(self.decided_at, field_name="决策时间"))
        object.__setattr__(self, "improvement_record_ids", tuple(dict.fromkeys(self.improvement_record_ids)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "rationale": self.rationale,
            "decided_at": self.decided_at,
            "allowed_scope": self.allowed_scope,
            "improvement_record_ids": list(self.improvement_record_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Decision":
        return cls(
            kind=data["kind"],
            rationale=data["rationale"],
            decided_at=data["decided_at"],
            allowed_scope=data.get("allowed_scope"),
            improvement_record_ids=tuple(data.get("improvement_record_ids", ())),
        )


@dataclass(frozen=True, slots=True)
class ReviewRevision:
    """一次冻结的评审样本；迟到数据只能触发新的修订，不能改写本修订。"""

    revision_no: int
    frozen_at: str
    sample: tuple[tuple[str, str], ...]  # (record_id, fingerprint)，按 record_id 排序
    stats: dict[str, Any]
    reason: str = ""
    conclusions: tuple[Conclusion, ...] = ()
    decision: Decision | None = None

    def __post_init__(self) -> None:
        if self.revision_no < 1:
            raise ValueError("修订号必须大于零")
        object.__setattr__(self, "frozen_at", require_iso8601(self.frozen_at, field_name="冻结时间"))
        object.__setattr__(self, "sample", tuple(tuple(pair) for pair in self.sample))
        object.__setattr__(self, "conclusions", tuple(self.conclusions))

    @property
    def sample_ids(self) -> tuple[str, ...]:
        return tuple(record_id for record_id, _ in self.sample)

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_no": self.revision_no,
            "frozen_at": self.frozen_at,
            "reason": self.reason,
            "sample": [list(pair) for pair in self.sample],
            "stats": self.stats,
            "conclusions": [c.to_dict() for c in self.conclusions],
            "decision": self.decision.to_dict() if self.decision else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewRevision":
        return cls(
            revision_no=int(data["revision_no"]),
            frozen_at=data["frozen_at"],
            reason=data.get("reason", ""),
            sample=tuple((pair[0], pair[1]) for pair in data["sample"]),
            stats=dict(data["stats"]),
            conclusions=tuple(Conclusion.from_dict(c) for c in data.get("conclusions", ())),
            decision=Decision.from_dict(data["decision"]) if data.get("decision") else None,
        )


@dataclass(frozen=True, slots=True)
class Review:
    """一次阶段评审；每次修订追加一个不可变的冻结样本。"""

    review_id: str
    product_code: str
    scope: Scope
    created_at: str
    revisions: tuple[ReviewRevision, ...]
    status: str = "open"

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_id", normalize_text(self.review_id, field_name="评审标识"))
        object.__setattr__(self, "product_code", normalize_text(self.product_code, field_name="产品编码"))
        object.__setattr__(self, "created_at", require_iso8601(self.created_at, field_name="创建时间"))
        object.__setattr__(self, "revisions", tuple(self.revisions))
        if not self.revisions:
            raise ValueError("评审必须至少包含一个冻结修订")
        if self.status not in ("open", "closed"):
            raise ValueError("评审状态必须是 open 或 closed")

    @property
    def latest(self) -> ReviewRevision:
        return self.revisions[-1]

    def with_revision(self, revision: ReviewRevision) -> "Review":
        return replace(self, revisions=self.revisions + (revision,))

    def with_updated_latest(self, revision: ReviewRevision) -> "Review":
        return replace(self, revisions=self.revisions[:-1] + (revision,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "product_code": self.product_code,
            "scope": self.scope.to_dict(),
            "created_at": self.created_at,
            "status": self.status,
            "revisions": [r.to_dict() for r in self.revisions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Review":
        return cls(
            review_id=data["review_id"],
            product_code=data["product_code"],
            scope=Scope.from_dict(data.get("scope")),
            created_at=data["created_at"],
            status=data.get("status", "open"),
            revisions=tuple(ReviewRevision.from_dict(r) for r in data["revisions"]),
        )


def sort_records(records: Iterable[FeedbackRecord]) -> list[FeedbackRecord]:
    """统一时间线排序：按发生时间，再按记录标识，保证输出确定。"""
    return sorted(records, key=lambda r: (r.occurred_at, r.record_id))
