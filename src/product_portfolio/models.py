"""新品市场适配复盘的领域模型。

统一登记口径：每条市场记录都携带 行业 / 工况 / 客户阶段 / 配置版本 四个细分维度，
机会、反馈、采用、失单、现场问题、改进承诺全部归并到同一时间线。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def require_text(value: Any, field_name: str) -> str:
    """校验非空文本并去掉首尾空白。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    return value.strip()


def canonical_fingerprint(payload: dict[str, Any]) -> str:
    """生成稳定内容摘要，供幂等、冻结和审计使用。"""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(text.encode("utf-8")).hexdigest()


def parse_instant(value: Any, field_name: str = "occurred_at") -> datetime:
    """把 ISO 8601 文本解析为 UTC 时间；缺少时区时按 UTC 处理。"""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field_name} 不是合法的 ISO 时间: {value!r}") from None
    else:
        raise ValueError(f"{field_name} 不是合法的 ISO 时间: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_instant(moment: datetime) -> str:
    """统一为 UTC ISO 文本，保证排序与比较口径一致。"""
    return moment.astimezone(timezone.utc).isoformat()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class RecordKind(str, Enum):
    """时间线记录类型。"""

    OPPORTUNITY = "opportunity"                    # 机会登记
    FEEDBACK = "feedback"                          # 反馈（试装/报价/故障/维护）
    ADOPTION = "adoption"                          # 可验证的采用结果
    LOST_ORDER = "lost_order"                      # 失单原因
    FIELD_ISSUE = "field_issue"                    # 现场问题
    IMPROVEMENT_COMMITMENT = "improvement_commitment"  # 改进承诺


class FeedbackChannel(str, Enum):
    TRIAL = "trial"              # 试装
    QUOTE = "quote"              # 报价
    FAILURE = "failure"          # 故障
    MAINTENANCE = "maintenance"  # 维护


class FindingKind(str, Enum):
    FACT = "fact"                # 事实：必须有冻结样本内的记录佐证
    INFERENCE = "inference"      # 推断：由事实记录推导
    HYPOTHESIS = "hypothesis"    # 待验证假设：必须附验证计划


class DecisionAction(str, Enum):
    CONTINUE = "continue"    # 继续推广
    RESTRICT = "restrict"    # 限制场景
    IMPROVE = "improve"      # 安排改进
    EXIT = "exit"            # 退出


class ReviewStatus(str, Enum):
    FROZEN = "frozen"    # 已冻结样本，可登记结论
    DECIDED = "decided"  # 已决策，评审不可再修改


ISSUE_SEVERITIES = ("low", "medium", "high", "critical")
ISSUE_STATUSES = ("open", "resolved")
COMMITMENT_STATUSES = ("promised", "fulfilled", "cancelled")


# ---------------------------------------------------------------------------
# 细分维度
# ---------------------------------------------------------------------------

_SEGMENT_FIELDS = ("industry", "condition", "customer_stage", "config_version")


@dataclass(frozen=True, slots=True)
class SegmentFilter:
    """细分筛选器；留空的维度不参与过滤。

    用于评审范围、结论适用范围和对比分组，保证结论不跨细分市场套用。
    """

    industry: str | None = None
    condition: str | None = None
    customer_stage: str | None = None
    config_version: str | None = None

    def __post_init__(self) -> None:
        for name in _SEGMENT_FIELDS:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_text(value, name))

    def matches(self, record: "MarketRecord") -> bool:
        for name in _SEGMENT_FIELDS:
            wanted = getattr(self, name)
            if wanted is not None and getattr(record, name) != wanted:
                return False
        return True

    def compatible_with(self, outer: "SegmentFilter") -> bool:
        """判断本筛选器是否与外层范围冲突（只允许保持一致或进一步收窄）。"""
        for name in _SEGMENT_FIELDS:
            outer_value = getattr(outer, name)
            inner_value = getattr(self, name)
            if outer_value is not None and inner_value is not None and outer_value != inner_value:
                return False
        return True

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _SEGMENT_FIELDS if getattr(self, name) is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SegmentFilter":
        if not data:
            return cls()
        unknown = set(data) - set(_SEGMENT_FIELDS)
        if unknown:
            raise ValueError(f"未知细分维度: {sorted(unknown)}")
        return cls(**{name: data.get(name) for name in _SEGMENT_FIELDS})


# ---------------------------------------------------------------------------
# 主数据
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Product:
    product_code: str
    name: str
    family: str = ""
    launched_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_code", require_text(self.product_code, "product_code"))
        object.__setattr__(self, "name", require_text(self.name, "name"))
        if self.launched_at is not None:
            object.__setattr__(self, "launched_at", format_instant(parse_instant(self.launched_at, "launched_at")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_code": self.product_code,
            "name": self.name,
            "family": self.family,
            "launched_at": self.launched_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Product":
        return cls(
            product_code=data["product_code"],
            name=data["name"],
            family=data.get("family", ""),
            launched_at=data.get("launched_at"),
        )


@dataclass(frozen=True, slots=True)
class ConfigVersion:
    """产品配置版本。配置升级只新增版本，历史反馈永远留在原版本上。"""

    product_code: str
    version: str
    supersedes: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_code", require_text(self.product_code, "product_code"))
        object.__setattr__(self, "version", require_text(self.version, "version"))
        if self.supersedes is not None:
            object.__setattr__(self, "supersedes", require_text(self.supersedes, "supersedes"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_code": self.product_code,
            "version": self.version,
            "supersedes": self.supersedes,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigVersion":
        return cls(
            product_code=data["product_code"],
            version=data["version"],
            supersedes=data.get("supersedes"),
            note=data.get("note", ""),
        )


# ---------------------------------------------------------------------------
# 市场记录（统一时间线条目）
# ---------------------------------------------------------------------------

def _validate_details(kind: RecordKind, details: dict[str, Any]) -> dict[str, Any]:
    """按记录类型校验并规范化负载，返回规范化后的新字典。"""
    if not isinstance(details, dict):
        raise ValueError("details 必须是对象")
    normalized = dict(details)

    if kind is RecordKind.FEEDBACK:
        channel = normalized.get("channel")
        try:
            normalized["channel"] = FeedbackChannel(channel).value
        except ValueError:
            allowed = ", ".join(c.value for c in FeedbackChannel)
            raise ValueError(f"反馈渠道 channel 必须是 {allowed} 之一") from None
        require_text(normalized.get("summary"), "反馈摘要 summary")

    elif kind is RecordKind.ADOPTION:
        verified = normalized.get("verified")
        if not isinstance(verified, bool):
            raise ValueError("采用结果必须显式标注 verified 布尔值")
        if verified:
            require_text(normalized.get("evidence"), "已验证采用必须提供 evidence 佐证")
        units = normalized.get("units")
        if units is not None and (not isinstance(units, int) or isinstance(units, bool) or units < 1):
            raise ValueError("采用数量 units 必须是正整数")

    elif kind is RecordKind.LOST_ORDER:
        require_text(normalized.get("reason"), "失单原因 reason")

    elif kind is RecordKind.FIELD_ISSUE:
        severity = normalized.get("severity", "medium")
        if severity not in ISSUE_SEVERITIES:
            raise ValueError(f"现场问题严重度 severity 必须是 {ISSUE_SEVERITIES} 之一")
        status = normalized.get("status", "open")
        if status not in ISSUE_STATUSES:
            raise ValueError(f"现场问题状态 status 必须是 {ISSUE_STATUSES} 之一")
        normalized.setdefault("severity", "medium")
        normalized.setdefault("status", "open")
        require_text(normalized.get("summary"), "现场问题摘要 summary")

    elif kind is RecordKind.IMPROVEMENT_COMMITMENT:
        require_text(normalized.get("text"), "改进承诺内容 text")
        status = normalized.get("status", "promised")
        if status not in COMMITMENT_STATUSES:
            raise ValueError(f"改进承诺状态 status 必须是 {COMMITMENT_STATUSES} 之一")
        normalized.setdefault("status", "promised")
        due = normalized.get("due")
        if due is not None:
            normalized["due"] = format_instant(parse_instant(due, "due"))

    return normalized


@dataclass(frozen=True, slots=True)
class MarketRecord:
    """统一时间线上的一条市场记录。

    record_id 是来源系统的稳定业务标识，配合内容指纹实现幂等导入：
    同标识同内容视为重复，同标识不同内容视为冲突。
    """

    record_id: str
    product_code: str
    industry: str
    condition: str
    customer_stage: str
    config_version: str
    kind: RecordKind
    occurred_at: str
    source: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", require_text(self.record_id, "record_id"))
        object.__setattr__(self, "product_code", require_text(self.product_code, "product_code"))
        object.__setattr__(self, "industry", require_text(self.industry, "industry"))
        object.__setattr__(self, "condition", require_text(self.condition, "condition"))
        object.__setattr__(self, "customer_stage", require_text(self.customer_stage, "customer_stage"))
        object.__setattr__(self, "config_version", require_text(self.config_version, "config_version"))
        object.__setattr__(self, "source", require_text(self.source, "source"))
        try:
            kind = RecordKind(self.kind)
        except ValueError:
            allowed = ", ".join(k.value for k in RecordKind)
            raise ValueError(f"记录类型 kind 必须是 {allowed} 之一") from None
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "occurred_at", format_instant(parse_instant(self.occurred_at)))
        object.__setattr__(self, "details", _validate_details(kind, self.details))

    def business_dict(self) -> dict[str, Any]:
        """参与指纹计算的业务内容（不含入库时间等系统字段）。"""
        return {
            "record_id": self.record_id,
            "product_code": self.product_code,
            "industry": self.industry,
            "condition": self.condition,
            "customer_stage": self.customer_stage,
            "config_version": self.config_version,
            "kind": self.kind.value,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "details": self.details,
        }

    def fingerprint(self) -> str:
        return canonical_fingerprint(self.business_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.business_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketRecord":
        return cls(
            record_id=data["record_id"],
            product_code=data["product_code"],
            industry=data["industry"],
            condition=data["condition"],
            customer_stage=data["customer_stage"],
            config_version=data["config_version"],
            kind=data["kind"],
            occurred_at=data["occurred_at"],
            source=data["source"],
            details=data.get("details", {}),
        )


# ---------------------------------------------------------------------------
# 阶段评审
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Finding:
    """评审结论。证据必须全部来自冻结样本，且不得跨细分范围。"""

    finding_id: str
    kind: FindingKind
    text: str
    segment_scope: SegmentFilter
    evidence: tuple[str, ...]
    verification_plan: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "kind": self.kind.value,
            "text": self.text,
            "segment_scope": self.segment_scope.to_dict(),
            "evidence": list(self.evidence),
            "verification_plan": self.verification_plan,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        return cls(
            finding_id=data["finding_id"],
            kind=FindingKind(data["kind"]),
            text=data["text"],
            segment_scope=SegmentFilter.from_dict(data.get("segment_scope")),
            evidence=tuple(data.get("evidence", ())),
            verification_plan=data.get("verification_plan"),
            created_at=data["created_at"],
        )


@dataclass(slots=True)
class Decision:
    action: DecisionAction
    rationale: str
    decided_at: str
    restrictions: tuple[SegmentFilter, ...] = ()
    commitment_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "rationale": self.rationale,
            "decided_at": self.decided_at,
            "restrictions": [scope.to_dict() for scope in self.restrictions],
            "commitment_ids": list(self.commitment_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Decision":
        return cls(
            action=DecisionAction(data["action"]),
            rationale=data["rationale"],
            decided_at=data["decided_at"],
            restrictions=tuple(SegmentFilter.from_dict(item) for item in data.get("restrictions", ())),
            commitment_ids=tuple(data.get("commitment_ids", ())),
        )


@dataclass(slots=True)
class Review:
    """阶段评审：创建即冻结当时样本与统计，之后只能追加结论、登记一次决策。

    冻结后到达的迟到数据不会改写 sample / stats，只能通过修订生成新版本。
    """

    review_id: str
    product_code: str
    gate: str
    scope: SegmentFilter
    revision_no: int
    revision_of: str | None
    status: ReviewStatus
    frozen_at: str
    sample: tuple[dict[str, str], ...]  # {"record_id", "fingerprint"}
    stats: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    decision: Decision | None = None

    @property
    def sample_ids(self) -> tuple[str, ...]:
        return tuple(entry["record_id"] for entry in self.sample)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "product_code": self.product_code,
            "gate": self.gate,
            "scope": self.scope.to_dict(),
            "revision_no": self.revision_no,
            "revision_of": self.revision_of,
            "status": self.status.value,
            "frozen_at": self.frozen_at,
            "sample": list(self.sample),
            "stats": self.stats,
            "findings": [finding.to_dict() for finding in self.findings],
            "decision": self.decision.to_dict() if self.decision else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Review":
        return cls(
            review_id=data["review_id"],
            product_code=data["product_code"],
            gate=data["gate"],
            scope=SegmentFilter.from_dict(data.get("scope")),
            revision_no=data["revision_no"],
            revision_of=data.get("revision_of"),
            status=ReviewStatus(data["status"]),
            frozen_at=data["frozen_at"],
            sample=tuple(dict(entry) for entry in data.get("sample", ())),
            stats=data.get("stats", {}),
            findings=[Finding.from_dict(item) for item in data.get("findings", ())],
            decision=Decision.from_dict(data["decision"]) if data.get("decision") else None,
        )
