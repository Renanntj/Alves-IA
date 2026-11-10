from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_AUDIO_BYTES = 16 * 1024 * 1024
CONFIDENCE_THRESHOLD = -1.0
_MEDIA_HOST = "lookaside.fbsbx.com"
_VERSION_PATTERN = re.compile(r"v\d+\.\d+")
_MEDIA_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}")
_MIME_SUFFIXES = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/mpga": ".mpga",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
}


class AudioMediaError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AudioFile:
    content: bytes
    mime_type: str
    filename: str


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None

    @property
    def confident(self) -> bool:
        return (
            bool(self.text.strip())
            and self.confidence is not None
            and (self.confidence >= CONFIDENCE_THRESHOLD)
        )


def _trusted_media_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise AudioMediaError("unsafe_media_url")
    try:
        parsed = urlparse(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == _MEDIA_HOST
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and parsed.path.startswith("/whatsapp_business/attachments/")
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise AudioMediaError("unsafe_media_url")
    return value


def download_audio(
    media_id: str,
    token: str,
    version: str,
    transport: httpx.BaseTransport | None = None,
) -> AudioFile:
    if not token or not _VERSION_PATTERN.fullmatch(version):
        raise AudioMediaError("media_configuration")
    if not _MEDIA_ID_PATTERN.fullmatch(media_id):
        raise AudioMediaError("invalid_media_id")

    headers = {"Authorization": f"Bearer {token}"}
    metadata_endpoint = f"https://graph.facebook.com/{version}/{media_id}"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0), follow_redirects=False, transport=transport
        ) as client:
            metadata_response = client.get(metadata_endpoint, headers=headers)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            if not isinstance(metadata, dict):
                raise AudioMediaError("invalid_media_metadata")
            mime_value = metadata.get("mime_type")
            if not isinstance(mime_value, str):
                raise AudioMediaError("unsupported_media_type")
            mime_type = mime_value.split(";", 1)[0].strip().lower()
            suffix = _MIME_SUFFIXES.get(mime_type)
            if suffix is None:
                raise AudioMediaError("unsupported_media_type")
            size = metadata.get("file_size")
            if type(size) is not int or size <= 0:
                raise AudioMediaError("invalid_media_size")
            if size > MAX_AUDIO_BYTES:
                raise AudioMediaError("media_too_large")
            expected_hash = metadata.get("sha256")
            if not isinstance(expected_hash, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", expected_hash
            ):
                raise AudioMediaError("invalid_media_hash")
            media_url = _trusted_media_url(metadata.get("url"))

            content = bytearray()
            with client.stream("GET", media_url, headers=headers) as response:
                response.raise_for_status()
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    if not declared_length.isdigit():
                        raise AudioMediaError("invalid_media_size")
                    if int(declared_length) > MAX_AUDIO_BYTES:
                        raise AudioMediaError("media_too_large")
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_AUDIO_BYTES:
                        raise AudioMediaError("media_too_large")
    except AudioMediaError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise AudioMediaError("media_download_failed") from exc

    if len(content) != size:
        raise AudioMediaError("media_size_mismatch")
    digest = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(digest, expected_hash.lower()):
        raise AudioMediaError("media_hash_mismatch")
    return AudioFile(bytes(content), mime_type, f"whatsapp-audio{suffix}")


def parse_transcript(text: str, segment_scores: list[float]) -> Transcript:
    confidence = sum(segment_scores) / len(segment_scores) if segment_scores else None
    return Transcript(text=text.strip()[:4000], confidence=confidence)


def transcribe_audio(
    audio: AudioFile,
    api_key: str,
    model: str = "whisper-1",
    transport: httpx.BaseTransport | None = None,
) -> Transcript:
    if not api_key:
        raise AudioMediaError("transcription_configuration")
    from openai import OpenAI

    http_client = httpx.Client(transport=transport) if transport is not None else None
    try:
        with OpenAI(
            api_key=api_key,
            timeout=30,
            max_retries=0,
            http_client=http_client,
        ) as client:
            response = client.audio.transcriptions.create(
                model=model,
                file=(audio.filename, audio.content, audio.mime_type),
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
    finally:
        if http_client is not None:
            http_client.close()

    response_data: Any = response
    raw_segments = getattr(response_data, "segments", None)
    scores = [
        float(score)
        for segment in (raw_segments or [])
        if isinstance((score := getattr(segment, "avg_logprob", None)), (int, float))
        and not isinstance(score, bool)
    ]
    return parse_transcript(str(getattr(response_data, "text", "")), scores)
