# WhatsApp Audio Transcription Implementation Plan

> **For agentic workers:** Execute this plan inline, task by task. The repository owner manages Git commits.

**Goal:** Transcribe supported WhatsApp audio safely and pass only valid, sufficiently confident text into the existing isolated agent flow.

**Architecture:** The webhook remains fast and durable. When live AI is enabled, ingestion adds a `transcribe_audio` Outbox job. A worker serializes on the conversation lock, fetches the temporary Meta media URL, downloads bytes only from the exact approved Meta media host with redirects disabled, verifies MIME, size and SHA-256, and calls OpenAI Whisper. Valid transcripts become normal user conversation messages and queue the existing agent turn; unsupported or low-confidence results create an audited handoff. Audio bytes and temporary URLs are never persisted.

**Tech Stack:** FastAPI, SQLAlchemy 2, PostgreSQL/RLS, Celery/Redis, HTTPX, OpenAI Python SDK (`whisper-1`).

**Spec:** Product requirements in the project prompt; WhatsApp and AI foundations in `docs/phase-2.md` and `src/alves_ia/whatsapp/processing.py`.

## Global Constraints

- Keep `AI_LIVE_ENABLED=false` by default.
- Preserve strict clinic isolation with transaction-local `clinic_id` and forced RLS.
- Never expose tokens, signed media URLs, raw provider errors, or audio bytes in logs/database payloads.
- Never follow media URL redirects; accept only HTTPS on `lookaside.fbsbx.com`.
- Bound downloads to 16 MiB and support only formats accepted by the transcription API.
- Continue the existing rule that the bot only replies while `BOT_ACTIVE`.
- Do not run real provider or Meta calls in automated tests.

## Review Focus

- Invalid or hostile media URL: client rejects scheme, host, port, path and redirects before forwarding the bearer token.
- Oversized or mismatched file: bounded stream and SHA-256 verification reject it without storage.
- Unsupported MIME or absent confidence data: handoff is audited and no agent-turn job is queued.
- Conversation handed to a human during queued processing: lock/state gate prevents transcription reply.
- Provider failure: sanitized error state and audited handoff; no unbounded retry or raw error persistence.

---

### Task 1: Secure media download and transcription helpers

**Files:** Create `src/alves_ia/whatsapp/audio.py`; modify `src/alves_ia/config.py`, `.env.example`, `compose.yaml`; test `tests/test_audio.py`.

**Interfaces:** `download_audio(media_id, token, version, transport=None) -> AudioFile`; `transcribe_audio(audio, api_key, model='whisper-1', transport=None) -> Transcript`. `Transcript` exposes text and a confidence result computed from Whisper verbose segments; missing segment confidence is low confidence.

- [x] Write tests for URL host/scheme/path validation, redirect rejection, 16 MiB cap, MIME and hash validation, and confidence behavior.
- [x] Run tests and confirm expected failures.
- [x] Implement bounded, non-redirecting download and Whisper adapter.
- [x] Run focused tests, Ruff and MyPy.

### Task 2: Durable audio job and conversation handoff

**Files:** Modify `src/alves_ia/whatsapp/service.py`, `src/alves_ia/whatsapp/processing.py`, `migrations/versions/0005_audio_jobs.py`; test `tests/test_whatsapp_integration.py`.

- [x] Add integration expectations for audio event ingestion, one transcription job, transcript-to-agent job, and handoff on low confidence.
- [ ] Run the PostgreSQL test to confirm the new expectations fail first; Docker/PostgreSQL is required.
- [x] Add the new Outbox kind through a migration and enqueue it only when live AI is enabled and the conversation is bot-owned.
- [x] Process audio under the conversation lock; validate current state before provider calls; persist only transcript text; create agent-turn only for confident transcripts.
- [x] For unsupported, low-confidence, or provider/download failure cases, make an audited human handoff with sanitized job error codes.
- [ ] Run the PostgreSQL integration suite; local unit suite, Ruff and MyPy pass.

### Task 3: Document operating requirements

**Files:** Modify `README.md`, `docs/phase-2.md`; create `docs/phase-3.md`.

- [x] Document the separate Whisper key requirement when Gemini is the chat provider, size/MIME limits, opt-in, confidence fallback and lack of production approval.
- [x] Verify docs match the configuration and local test outcomes.
