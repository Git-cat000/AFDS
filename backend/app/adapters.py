from pathlib import Path
from typing import Protocol

from app.errors import DomainError


class MediaAdapter(Protocol):
    provider_id: str

    def describe(self) -> dict: ...

    def generate(self, request: dict, output: Path, checkpoint) -> dict: ...


def providers(settings):
    try:
        settings.binary("ffmpeg")
        settings.binary("ffprobe")
        availability = "available"
    except FileNotFoundError:
        availability = "unavailable"
    return [
        {
            "provider_id": "mock-director",
            "display_name": "Mock Director",
            "backend_type": "mock",
            "availability": "available",
            "capabilities": ["storyboard"],
            "pricing": {"mode": "free_mock"},
            "adapter_version": "0.1.0",
            "config_required": [],
        },
        {
            "provider_id": "mock-video-local",
            "display_name": "Mock Video · FFmpeg",
            "backend_type": "mock",
            "availability": availability,
            "capabilities": ["text_to_video"],
            "pricing": {"mode": "free_mock"},
            "constraints": {
                "duration_seconds_min": 1,
                "duration_seconds_max": 30,
                "supports_cancel": True,
                "supports_seed": False,
                "max_width": 1920,
                "max_height": 1080,
            },
            "adapter_version": "0.1.0",
            "config_required": ["ffmpeg", "ffprobe"],
        },
    ]


def check_provider(settings, provider_id, capability):
    provider = next((p for p in providers(settings) if p["provider_id"] == provider_id), None)
    if provider is None or provider["availability"] != "available":
        raise DomainError("MODEL_UNAVAILABLE", "Provider is unavailable or not configured", 422)
    if capability not in provider["capabilities"]:
        raise DomainError(
            "CAPABILITY_UNSUPPORTED", "Provider does not support this capability", 422
        )
    return provider


def video_adapter(settings, provider_id) -> MediaAdapter:
    """Resolve adapters at the boundary so the worker does not select concrete models."""
    from app.media import MockVideo

    check_provider(settings, provider_id, "text_to_video")
    factories = {"mock-video-local": MockVideo}
    return factories[provider_id](settings)
