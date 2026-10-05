"""新品组合市场适配复盘领域包。"""

from .contracts import MarketRelease, unique_by_identity
from .models import (
    Conclusion,
    ConfigVersion,
    CustomerStage,
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
    Severity,
)
from .service import (
    ConflictError,
    ImportResult,
    NotFoundError,
    PortfolioService,
    StateError,
    ValidationError,
)
from .store import InMemoryStore, JsonFileStore

__all__ = [
    "MarketRelease",
    "unique_by_identity",
    "Product",
    "ConfigVersion",
    "Opportunity",
    "FeedbackRecord",
    "FeedbackKind",
    "CustomerStage",
    "Severity",
    "EvidenceClass",
    "DecisionKind",
    "Conclusion",
    "Decision",
    "Scope",
    "Review",
    "ReviewRevision",
    "PortfolioService",
    "ImportResult",
    "ValidationError",
    "ConflictError",
    "StateError",
    "NotFoundError",
    "InMemoryStore",
    "JsonFileStore",
]
