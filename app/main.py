"""ViveEnglish — FastAPI application entry point.

Run:
    uvicorn app.main:app --reload --port 8000
or simply:
    python run.py
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, content_store, database, foundry

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)


@app.on_event("startup")
def _startup() -> None:
    database.init_db()
    foundry.init()  # best-effort probe; never fatal
    if config.MANAGE_FOUNDRY and config.AUTOLOAD_MODEL:
        foundry.ensure_model_async()  # first-launch model download (non-blocking)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class TranslateReq(BaseModel):
    text: str
    mode: str = "auto"          # auto|word|sentence|phrase
    lesson_id: str | None = None


class ChatReq(BaseModel):
    messages: list[dict[str, str]]   # [{role, content}]
    scenario: str = ""
    level: str = "beginner"
    tutor_name: str = "Vivi"
    gender: str = "female"


class SpeechReq(BaseModel):
    target: str
    said: str
    level: str = "beginner"


class ProgressReq(BaseModel):
    lesson_id: str
    status: str | None = None        # in_progress|completed
    score: int | None = None
    minutes: float = 0


class ProfileReq(BaseModel):
    display_name: str | None = None
    level: str | None = None
    art_style: str | None = None
    tutor_gender: str | None = None
    daily_goal: int | None = None
    settings: dict[str, Any] | None = None


class SaveWordReq(BaseModel):
    word: str
    meaning: str
    lesson_id: str | None = None


# ---------------------------------------------------------------------------
# Meta / health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"app": config.APP_NAME, "version": config.APP_VERSION,
            "ai": foundry.status()}


@app.post("/api/ai/reconnect")
def ai_reconnect() -> dict[str, Any]:
    foundry.init()
    return foundry.status()


@app.get("/api/ai/setup-state")
def ai_setup_state() -> dict[str, Any]:
    return foundry.setup_state()


@app.post("/api/ai/setup")
def ai_setup() -> dict[str, Any]:
    """Start (or report) the first-launch model/EP download."""
    return foundry.ensure_model_async()


@app.get("/api/speech/status")
def speech_status() -> dict[str, Any]:
    foundry.init()
    return foundry.speech_status(init_manager=True)


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------
@app.get("/api/themes")
def get_themes() -> list[dict[str, str]]:
    return content_store.themes()


@app.get("/api/lessons")
def get_lessons() -> dict[str, Any]:
    return {"lessons": content_store.list_lessons(),
            "progress": database.get_all_progress()}


@app.get("/api/lessons/{lesson_id}")
def get_lesson(lesson_id: str) -> dict[str, Any]:
    ls = content_store.get_lesson(lesson_id)
    if not ls:
        raise HTTPException(404, "lesson not found")
    return ls


@app.get("/api/art-styles")
def get_art_styles() -> dict[str, Any]:
    return content_store.art_styles()


@app.get("/api/lessons/{lesson_id}/illustration")
def get_illustration(lesson_id: str, style: str | None = None) -> dict[str, Any]:
    style_key = style or database.get_profile().get("art_style")
    prompt = content_store.build_illustration_prompt(lesson_id, style_key)
    if not prompt:
        raise HTTPException(404, "lesson not found")
    return prompt


# ---------------------------------------------------------------------------
# AI features
# ---------------------------------------------------------------------------
@app.post("/api/translate")
def translate(req: TranslateReq) -> dict[str, Any]:
    glossary = content_store.lesson_glossary(req.lesson_id) if req.lesson_id else None
    return foundry.translate(req.text, req.mode, glossary)


@app.post("/api/chat")
def chat(req: ChatReq) -> dict[str, Any]:
    if req.messages:
        database.log_activity("chat", detail=req.scenario)
    return foundry.tutor_reply(req.messages, req.scenario, req.level,
                               name=req.tutor_name, gender=req.gender)


@app.post("/api/speech/check")
def speech_check(req: SpeechReq) -> dict[str, Any]:
    result = foundry.check_speech(req.target, req.said, req.level)
    database.log_activity("speak", detail=req.target[:120])
    return result


@app.post("/api/speech/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict[str, Any]:
    """Transcribe an uploaded 16kHz mono WAV clip with a local Whisper model.

    The frontend records WebM/Opus and converts to 16k mono WAV via the Web
    Audio API before upload. If STT is unavailable we return a clear notice so
    the UI can ask the learner to type what they said instead.
    """
    data = await audio.read()
    return foundry.transcribe(data)


# ---------------------------------------------------------------------------
# Progress / profile
# ---------------------------------------------------------------------------
@app.get("/api/progress")
def progress() -> dict[str, Any]:
    return {
        "profile": database.get_profile(),
        "progress": database.get_all_progress(),
        "activity": database.get_activity(60),
        "saved_words": database.get_saved_words(),
    }


@app.post("/api/progress")
def post_progress(req: ProgressReq) -> dict[str, Any]:
    row = database.record_progress(req.lesson_id, req.status, req.score)
    database.log_activity(
        "quiz" if req.score is not None else "study",
        lesson_id=req.lesson_id, minutes=req.minutes,
    )
    return row


@app.get("/api/profile")
def get_profile() -> dict[str, Any]:
    return database.get_profile()


@app.post("/api/profile")
def post_profile(req: ProfileReq) -> dict[str, Any]:
    return database.update_profile(**req.model_dump(exclude_none=True))


@app.post("/api/words")
def add_word(req: SaveWordReq) -> dict[str, Any]:
    database.save_word(req.word, req.meaning, req.lesson_id)
    return {"ok": True, "saved_words": database.get_saved_words()}


@app.delete("/api/words/{word}")
def remove_word(word: str) -> dict[str, Any]:
    database.delete_word(word)
    return {"ok": True, "saved_words": database.get_saved_words()}


# ---------------------------------------------------------------------------
# Static frontend (mounted last so /api/* wins)
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
