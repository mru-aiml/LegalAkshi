"""Stage 2 Package Intelligence vision services."""
from app.services.vision.base import VisionError, VisionProvider
from app.services.vision.mock_provider import MockVisionProvider
from app.services.vision.provider import (
    VISION_AVAILABLE,
    VISION_DISABLED,
    VISION_INVALID_CONFIGURATION,
    VISION_NOT_CONFIGURED,
    VISION_UNREACHABLE,
    describe_vision_status,
    get_vision_config,
    get_vision_provider,
)
from app.services.vision.schemas import (
    MAX_VISION_CALLS_PER_INSPECTION,
    VISION_FIELD_GROUPS,
    VISION_STATUSES,
    groups_for_fields,
    validate_vision_candidate,
)
from app.services.vision.service import clear_vision_cache, extract_with_vision

__all__ = [
    "VisionError", "VisionProvider", "MockVisionProvider",
    "get_vision_config", "get_vision_provider", "describe_vision_status",
    "VISION_AVAILABLE", "VISION_DISABLED", "VISION_INVALID_CONFIGURATION",
    "VISION_NOT_CONFIGURED", "VISION_UNREACHABLE",
    "MAX_VISION_CALLS_PER_INSPECTION", "VISION_FIELD_GROUPS",
    "VISION_STATUSES", "groups_for_fields", "validate_vision_candidate",
    "clear_vision_cache", "extract_with_vision",
]
