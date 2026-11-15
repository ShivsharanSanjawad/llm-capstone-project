"""Local-only audio transcription; reviewed entries use the existing meeting API."""
import logging
import os
from pathlib import Path
import threading
import time

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/meetings", tags=["Meeting audio"])
MAX_BYTES = 50 * 1024 * 1024
MAX_SECONDS = 30 * 60
SAMPLE_RATE = 16000
_lock = threading.Lock()
_model = None
log = logging.getLogger(__name__)


def failure(status, code, message):
    return HTTPException(status, detail={"code": code, "message": message})


def decode_audio(file):
    """Bound decoded memory/duration, even for highly compressed uploads."""
    import av
    import numpy as np

    chunks, samples = [], 0
    try:
        with av.open(file, mode="r") as container:
            if not container.streams.audio:
                raise ValueError("No audio stream")
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    data = converted.to_ndarray().reshape(-1)
                    samples += len(data)
                    if samples > MAX_SECONDS * SAMPLE_RATE:
                        raise failure(413, "audio_too_long", "Choose audio no longer than 30 minutes.")
                    chunks.append(data)
            for converted in resampler.resample(None):
                data = converted.to_ndarray().reshape(-1)
                samples += len(data)
                if samples > MAX_SECONDS * SAMPLE_RATE:
                    raise failure(413, "audio_too_long", "Choose audio no longer than 30 minutes.")
                chunks.append(data)
    except HTTPException:
        raise
    except Exception:
        raise failure(422, "invalid_audio", "Cannot decode this audio. Try a valid MP3, WAV or M4A recording.") from None
    if not samples:
        raise failure(422, "empty_audio", "The recording contains no audio samples.")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def timestamp(seconds):
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def transcribe_audio(file):
    """One CPU transcription at a time; keep blocking inference off the event loop."""
    global _model
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, detail={"code": "audio_busy", "message": "Another recording is being transcribed. Try again when it finishes."}, headers={"Retry-After": "10"})
    started = time.monotonic()
    try:
        audio = decode_audio(file)
        if _model is None:
            try:
                from faster_whisper import WhisperModel
                _model = WhisperModel(os.getenv("AUDIO_WHISPER_MODEL", "base"), device="cpu", compute_type="int8", cpu_threads=4, num_workers=1)
            except Exception:
                log.warning("audio_model_unavailable")
                raise failure(503, "audio_model_unavailable", "Install faster-whisper and allow the first model download, or set AUDIO_WHISPER_MODEL to a downloaded model directory.") from None
        try:
            segments, info = _model.transcribe(audio, beam_size=1, vad_filter=True, condition_on_previous_text=False)
            entries = []
            for segment in segments:
                text = segment.text.strip()
                if text:
                    entries.append({"text": text, "timestamp": timestamp(segment.start), "speaker": None, "source": "audio"})
            if not entries:
                raise failure(422, "audio_no_speech", "No speech was detected. Try a clearer recording or upload a transcript.")
        except HTTPException:
            raise
        except Exception:
            log.warning("audio_transcription_failed")
            raise failure(422, "audio_transcription_failed", "Transcription failed. Try a shorter, clearer recording or upload a transcript.") from None
        log.info("audio_transcribed segments=%d duration_seconds=%.1f elapsed_seconds=%.1f", len(entries), len(audio) / SAMPLE_RATE, time.monotonic() - started)
        return {"transcript": entries, "language": info.language, "duration_seconds": round(len(audio) / SAMPLE_RATE, 2), "speaker_mode": "manual"}
    finally:
        _lock.release()


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """Return an unsaved transcript for review. No LLM call or speaker guessing."""
    try:
        if Path(file.filename or "").suffix.lower() not in {".mp3", ".wav", ".m4a"}:
            raise failure(415, "unsupported_audio", "Choose an MP3, WAV or M4A file.")
        # Do not trust client MIME types or reported sizes. Decode real content later.
        size = 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise failure(413, "audio_too_large", "Choose audio smaller than 50 MB.")
        if not size:
            raise failure(422, "empty_audio", "The audio file is empty.")
        await file.seek(0)
        return await run_in_threadpool(transcribe_audio, file.file)
    finally:
        await file.close()
