"""新品组合市场适配复盘领域包。"""

from .contracts import MarketRelease, unique_by_identity
from .models import (
    ConfigVersion,
    Decision,
    DecisionAction,
    FeedbackChannel,
    Finding,
    FindingKind,
    MarketRecord,
    Product,
    RecordKind,
    Review,
    ReviewStatus,
    SegmentFilter,
)
from .service import ImportReport, PortfolioService
from .statistics import compute_metrics
from .store import JsonStore, StoredRecord

__all__ = [
    "MarketRelease",
    "unique_by_identity",
    "ConfigVersion",
    "Decision",
    "DecisionAction",
    "FeedbackChannel",
    "Finding",
    "FindingKind",
    "ImportReport",
    "JsonStore",
    "MarketRecord",
    "PortfolioService",
    "Product",
    "RecordKind",
    "Review",
    "ReviewStatus",
    "SegmentFilter",
    "StoredRecord",
    "compute_metrics",
]
