from __future__ import annotations

import hashlib
from collections.abc import Iterator
from types import SimpleNamespace

import httpx
import pytest

from alves_ia.whatsapp.audio import (
    MAX_AUDIO_BYTES,
    AudioFile,
    AudioMediaError,
    download_audio,
    parse_transcript,
    transcribe_audio,
)


def meta_metadata(
    audio: bytes, *, url: str = "https://lookaside.fbsbx.com/whatsapp_business/attachments/a"
) -> dict[str, object]:
    return {
        "url": url,
        "mime_type": "audio/ogg; codecs=opus",
        "file_size": len(audio),
        "sha256": hashlib.sha256(audio).hexdigest(),
    }


def test_download_audio_fetches_and_verifies_whatsapp_media() -> None:
    audio = b"ogg-opus-sample"
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json=meta_metadata(audio), request=request)
        return httpx.Response(200, content=audio, request=request)

    result = download_audio(
        "media-id", "access-token", "v23.0", transport=httpx.MockTransport(respond)
    )

    assert result.content == audio
    assert result.mime_type == "audio/ogg"
    assert result.filename.endswith(".ogg")
    assert len(requests) == 2
    assert all(request.headers["authorization"] == "Bearer access-token" for request in requests)


@pytest.mark.parametrize(
    "url",
    [
        "http://lookaside.fbsbx.com/whatsapp_business/attachments/a",
        "https://evil.example/whatsapp_business/attachments/a",
        "https://lookaside.fbsbx.com:8443/whatsapp_business/attachments/a",
        "https://lookaside.fbsbx.com.evil.example/whatsapp_business/attachments/a",
        "https://user@lookaside.fbsbx.com/whatsapp_business/attachments/a",
    ],
)
def test_download_audio_rejects_untrusted_media_urls(url: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=meta_metadata(b"sample", url=url), request=request)

    with pytest.raises(AudioMediaError, match="unsafe_media_url"):
        download_audio("media-id", "access-token", "v23.0", transport=httpx.MockTransport(respond))

    assert len(requests) == 1


def test_download_audio_does_not_follow_redirects() -> None:
    audio = b"ogg-opus-sample"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json=meta_metadata(audio), request=request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"}, request=request)

    with pytest.raises(AudioMediaError, match="media_download_failed"):
        download_audio("media-id", "access-token", "v23.0", transport=httpx.MockTransport(respond))


def test_download_audio_rejects_oversized_metadata_before_download() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                **meta_metadata(b"sample"),
                "file_size": MAX_AUDIO_BYTES + 1,
            },
            request=request,
        )

    with pytest.raises(AudioMediaError, match="media_too_large"):
        download_audio("media-id", "access-token", "v23.0", transport=httpx.MockTransport(respond))

    assert len(requests) == 1


def test_download_audio_stops_when_stream_exceeds_limit() -> None:
    requests: list[httpx.Request] = []

    class OversizedStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"x" * (MAX_AUDIO_BYTES + 1)

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json=meta_metadata(b"x"), request=request)
        return httpx.Response(200, stream=OversizedStream(), request=request)

    with pytest.raises(AudioMediaError, match="media_too_large"):
        download_audio("media-id", "access-token", "v23.0", transport=httpx.MockTransport(respond))

    assert len(requests) == 2


def test_download_audio_rejects_hash_mismatch() -> None:
    audio = b"modified"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json=meta_metadata(b"original"), request=request)
        return httpx.Response(200, content=audio, request=request)

    with pytest.raises(AudioMediaError, match="media_hash_mismatch"):
        download_audio("media-id", "access-token", "v23.0", transport=httpx.MockTransport(respond))


def test_download_audio_rejects_unsupported_mime_type() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={**meta_metadata(b"sample"), "mime_type": "audio/amr"},
            request=request,
        )

    with pytest.raises(AudioMediaError, match="unsupported_media_type"):
        download_audio("media-id", "access-token", "v23.0", transport=httpx.MockTransport(respond))


def test_transcript_confidence_requires_text_and_segment_scores() -> None:
    transcript = parse_transcript("Agende para amanhã", [-0.3, -0.5])
    low_confidence = parse_transcript("palavras incertas", [-1.8])
    missing_scores = parse_transcript("texto sem métricas", [])
    empty_text = parse_transcript("  ", [-0.2])

    assert transcript.confident
    assert not low_confidence.confident
    assert not missing_scores.confident
    assert not empty_text.confident


def test_transcribe_audio_uses_whisper_verbose_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs
            self.audio = SimpleNamespace(transcriptions=self)

        def __enter__(self) -> FakeOpenAI:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def create(self, **kwargs: object) -> object:
            captured["request"] = kwargs
            return SimpleNamespace(
                text="Palavras transcritas",
                segments=[SimpleNamespace(avg_logprob=-0.25)],
            )

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    audio = AudioFile(content=b"audio", filename="voice.ogg", mime_type="audio/ogg")

    result = transcribe_audio(audio, "test-key")

    request = captured["request"]
    assert isinstance(request, dict)
    assert request["model"] == "whisper-1"
    assert request["response_format"] == "verbose_json"
    assert request["timestamp_granularities"] == ["segment"]
    assert result.text == "Palavras transcritas"
    assert result.confident
