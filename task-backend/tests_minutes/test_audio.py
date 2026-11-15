"""Audio boundary and existing-pipeline regression tests; no downloads/API calls."""
import asyncio
from io import BytesIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import wave

import av
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import audio_processing as audio


def recording(extension="wav", seconds=1):
    if extension == "wav":
        target = BytesIO()
        with wave.open(target, "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            out.writeframes(b"\0\0" * (16000 * seconds))
        return target.getvalue()
    target = BytesIO()
    with av.open(target, mode="w", format="mp3" if extension == "mp3" else "mp4") as out:
        stream = out.add_stream("libmp3lame" if extension == "mp3" else "aac", rate=16000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 16000 * seconds), dtype=np.int16), format="s16", layout="mono")
        frame.sample_rate = 16000
        for packet in stream.encode(frame): out.mux(packet)
        for packet in stream.encode(None): out.mux(packet)
    return target.getvalue()


@pytest.fixture
def client(monkeypatch):
    class Model:
        def transcribe(self, data, **kwargs):
            assert data.dtype == np.float32
            return iter([NS(start=0, text=" We agreed to release on Friday. "), NS(start=.5, text="I will test the release."), NS(start=.9, text=" ")]), NS(language="en")
    monkeypatch.setattr(audio, "_model", Model())
    app = FastAPI(); app.include_router(audio.router)
    with TestClient(app) as c: yield c


@pytest.mark.parametrize("extension", ["mp3", "wav", "m4a"])
def test_formats_decode_and_return_reviewable_unknown_speakers(client, extension):
    response = client.post("/api/meetings/transcribe", files={"file": ("meeting." + extension, recording(extension), "application/octet-stream")})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["speaker_mode"] == "manual"
    assert data["transcript"][0] == {"text": "We agreed to release on Friday.", "timestamp": "00:00:00", "source": "audio", "speaker": None}
    assert len(data["transcript"]) == 2
    assert .9 <= data["duration_seconds"] <= 1.1


@pytest.mark.parametrize("name,body,status,code", [("bad.txt", b"hi", 415, "unsupported_audio"), ("empty.wav", b"", 422, "empty_audio"), ("corrupt.mp3", b"not audio", 422, "invalid_audio")])
def test_bad_uploads(client, name, body, status, code):
    response = client.post("/api/meetings/transcribe", files={"file": (name, body)})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_file_size_duration_and_concurrency_limits(client, monkeypatch):
    monkeypatch.setattr(audio, "MAX_BYTES", 10)
    assert client.post("/api/meetings/transcribe", files={"file": ("large.wav", recording())}).status_code == 413
    monkeypatch.setattr(audio, "MAX_BYTES", 500000)
    monkeypatch.setattr(audio, "MAX_SECONDS", 1)
    assert client.post("/api/meetings/transcribe", files={"file": ("long.wav", recording(seconds=2))}).status_code == 413
    audio._lock.acquire()
    try:
        response = client.post("/api/meetings/transcribe", files={"file": ("busy.wav", recording())})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "10"
    finally: audio._lock.release()


def test_empty_results_errors_and_lock_recovery(client, monkeypatch):
    class NoSpeech:
        def transcribe(self, *args, **kwargs): return iter([]), NS(language="en")
    monkeypatch.setattr(audio, "_model", NoSpeech())
    assert client.post("/api/meetings/transcribe", files={"file": ("silent.wav", recording())}).json()["detail"]["code"] == "audio_no_speech"
    class Broken:
        def transcribe(self, *args, **kwargs): raise RuntimeError("private path or key")
    monkeypatch.setattr(audio, "_model", Broken())
    response = client.post("/api/meetings/transcribe", files={"file": ("bad.wav", recording())})
    assert response.json()["detail"]["code"] == "audio_transcription_failed"
    assert "private" not in response.text
    assert not audio._lock.locked()


def test_model_load_failure_is_actionable(client, monkeypatch):
    import faster_whisper
    monkeypatch.setattr(audio, "_model", None)
    def fail(*args, **kwargs): raise RuntimeError("download secret")
    monkeypatch.setattr(faster_whisper, "WhisperModel", fail)
    response = client.post("/api/meetings/transcribe", files={"file": ("test.wav", recording())})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "audio_model_unavailable"
    assert "secret" not in response.text
    assert not audio._lock.locked()


def test_reviewed_audio_uses_existing_persistence_minutes_query_and_exports(client, monkeypatch):
    import combined_app
    import main
    from database import get_meeting_doc
    from models import FactualExtraction, ContextualInference, ExplicitTask
    from test_minutes import FakeLLM
    app_client = TestClient(combined_app.app)
    transcript = client.post("/api/meetings/transcribe", files={"file": ("meeting.wav", recording())}).json()["transcript"]
    transcript[0]["speaker"] = "Priya"  # A human assignment, never voice recognition.
    monkeypatch.setattr(main, "run_factual_extraction", AsyncMock(return_value=FactualExtraction(meeting_title="Audio review", date="2026-11-13", tasks=[ExplicitTask(task_id="T1", description="Test the release", explicit_owner=None)])))
    monkeypatch.setattr(main, "run_contextual_inference", AsyncMock(return_value=ContextualInference(enriched_tasks=[])))
    monkeypatch.setattr(combined_app.app.state.minutes_runtime, "llm", FakeLLM())
    response = app_client.post("/api/meetings/process", json={"meeting": {"title": "Audio review", "participants": ["Priya"]}, "transcript": transcript, "processing": {"source": "audio"}})
    assert response.status_code == 200, response.text
    meeting_id = response.json()["meeting_id"]
    stored = asyncio.run(get_meeting_doc(meeting_id))
    assert stored["transcript"] == [{**e, "at": None} for e in transcript]
    assert stored["processing_metadata"]["source"] == "audio"
    assert "[00:00:00] Priya:" in stored["raw_transcript"]
    assert stored["transcript"][1]["speaker"] is None
    result = app_client.post(f"/api/v1/minutes/generate/{meeting_id}")
    assert result.status_code == 201, result.text
    assert result.json()["segments"][0]["speaker"] == "Priya"
    assert app_client.get(f"/api/meetings/{meeting_id}").status_code == 200
    assert app_client.post(f"/api/meetings/{meeting_id}/query", json={"question": "What was this meeting about?"}).json()["claims"]
    assert app_client.get(f"/api/v1/minutes/{meeting_id}/export/pdf?draft=true").content.startswith(b"%PDF")
    assert app_client.get(f"/api/v1/minutes/{meeting_id}/export/xlsx?draft=true").content.startswith(b"PK")
