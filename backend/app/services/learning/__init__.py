"""Human-in-the-loop learning services (Stage 2, Parts L/M/N)."""
from app.services.learning.corrections import (
    apply_verified_corrections,
    build_correction,
    unresolved_report_value,
)
from app.services.learning.error_patterns import (
    aggregate_error_patterns,
    classify_failure,
)
from app.services.learning.evaluation import (
    export_verified_dataset,
    review_queue,
)
from app.services.learning.schemas import (
    FAILURE_TYPES,
    to_learning_example,
)

__all__ = [
    "apply_verified_corrections", "build_correction",
    "unresolved_report_value", "aggregate_error_patterns",
    "classify_failure", "export_verified_dataset", "review_queue",
    "FAILURE_TYPES", "to_learning_example",
]
