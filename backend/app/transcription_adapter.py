"""Timestamped file transcription using an explicitly enabled compatible profile."""

from decimal import Decimal, InvalidOperation

from pydantic import Field, ValidationError, model_validator

from app.provider_http import ProviderError, ProviderHTTP, reject_credential_output
from app.timeline_models import TimelineModel


class TranscriptionSegment(TimelineModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def positive_duration(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Segment ends before its start")
        return self


class TranscriptionResult(TimelineModel):
    language: str | None = Field(default=None, max_length=30)
    segments: list[TranscriptionSegment] = Field(max_length=500)

    @model_validator(mode="after")
    def ordered(self):
        previous = 0
        for segment in self.segments:
            if segment.start_ms < previous:
                raise ValueError("Segments must be ordered without overlap")
            previous = segment.end_ms
        return self


class TranscriptionAdapter:
    def __init__(self, config, source):
        self.config = config
        self.source = source

    def transcribe(self, request, output_dir, checkpoint) -> TranscriptionResult:
        if not self.config.get("transcription_timestamps"):
            raise ProviderError(
                "CAPABILITY_UNSUPPORTED", "Enable segment timestamps for this model"
            )
        fields = {
            "model": self.config["model"],
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if request.get("language"):
            fields["language"] = request["language"]
        with self.source.open("rb") as source:
            media_type = "audio/mpeg" if self.source.suffix == ".mp3" else "audio/wav"
            fields["file"] = (self.source.name, source, media_type)
            response = ProviderHTTP(self.config).json(
                "POST",
                "audio/transcriptions",
                multipart=fields,
                limit=2 * 1024**2,
                checkpoint=checkpoint,
            )
        reject_credential_output(response, self.config.get("api_key"))
        try:
            segments = response["segments"]
            if not isinstance(segments, list) or len(segments) > 500:
                raise ValueError("Select a shorter range with at most500segments")
            normalized = []
            for segment in segments:
                if isinstance(segment["start"], bool) or isinstance(segment["end"], bool):
                    raise ValueError("Invalid timestamp")
                start = Decimal(str(segment["start"])) * 1000
                end = Decimal(str(segment["end"])) * 1000
                if not start.is_finite() or not end.is_finite():
                    raise ValueError("Invalid timestamp")
                if start < 0 or end <= start or end > request["duration_seconds"] * 1000 + 1:
                    raise ValueError("Segment exceeds selected source range")
                normalized.append(
                    {"start_ms": round(start), "end_ms": round(end), "text": segment["text"]}
                )
            return TranscriptionResult(language=response.get("language"), segments=normalized)
        except (KeyError, TypeError, ValueError, InvalidOperation, ValidationError) as error:
            raise ProviderError(
                "TRANSCRIPTION_TIMESTAMPS_INVALID",
                "Expected up to500ordered timestamped segments within the selected range. "
                "Check model timestamp support or select a shorter range.",
            ) from error
