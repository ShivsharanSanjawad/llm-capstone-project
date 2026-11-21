import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union, Dict, Any, Type, TypeVar
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import httpx
from bson import ObjectId

from models import FactualExtraction, ContextualInference
from graph_engine import analyze_task_graph

# ---------------------------------------------------------------------------
# Configuration & Environment
# ---------------------------------------------------------------------------
# Check current directory, project root, and parent folders for .env
for env_candidate in [
    Path.cwd() / ".env",
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent.parent / ".env",
]:
    if env_candidate.is_file():
        load_dotenv(dotenv_path=env_candidate)
        break

# Determine LLM Provider
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL")

llm_api_key: Optional[str] = None
llm_base_url: str = ""
llm_model_name: str = ""
llm_provider_name: str = ""

if GEMINI_API_KEY:
    llm_api_key = GEMINI_API_KEY
    llm_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_model_name = LLM_MODEL or "gemini-2.5-flash"
    llm_provider_name = f"Google Gemini ({llm_model_name})"
elif GROQ_API_KEY:
    llm_api_key = GROQ_API_KEY
    llm_base_url = "https://api.groq.com/openai/v1"
    llm_model_name = LLM_MODEL or "openai/gpt-oss-120b"
    llm_provider_name = f"Groq ({llm_model_name})"
elif OPENAI_API_KEY:
    llm_api_key = OPENAI_API_KEY
    llm_base_url = "https://api.openai.com/v1"
    llm_model_name = LLM_MODEL or "gpt-4o-mini"
    llm_provider_name = f"OpenAI ({llm_model_name})"
else:
    print("[WARNING] No LLM API key found (GEMINI_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY).")

# ---------------------------------------------------------------------------
# Database Configuration (MongoDB Atlas with automatic local fallback)
# ---------------------------------------------------------------------------
MONGODB_URL = os.getenv("MONGODB_URL")
mongo_client = None
mongo_db = None
tasks_collection = None
db_mode = "local"

# Local file store path
LOCAL_STORAGE_DIR = Path(__file__).resolve().parent / "storage"
LOCAL_STORAGE_FILE = LOCAL_STORAGE_DIR / "meetings_store.json"

def init_local_storage():
    LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not LOCAL_STORAGE_FILE.exists():
        with open(LOCAL_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)

def read_local_storage() -> Dict[str, Any]:
    init_local_storage()
    try:
        with open(LOCAL_STORAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def write_local_storage(data: Dict[str, Any]):
    init_local_storage()
    with open(LOCAL_STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

# Storage helper functions that work seamlessly across MongoDB and local storage
async def save_meeting_doc(doc: Dict[str, Any]) -> str:
    global db_mode, tasks_collection
    if db_mode == "mongodb" and tasks_collection is not None:
        try:
            result = await tasks_collection.insert_one(doc)
            return str(result.inserted_id)
        except Exception as e:
            print(f"[Database] MongoDB insert error: {e}. Falling back to local storage.")
    
    # Local fallback
    store = read_local_storage()
    meeting_id = doc.get("_id") or str(uuid.uuid4())
    doc["_id"] = str(meeting_id)
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    store[str(meeting_id)] = doc
    write_local_storage(store)
    return str(meeting_id)

async def update_meeting_doc(meeting_id: str, updates: Dict[str, Any]) -> bool:
    global db_mode, tasks_collection
    if db_mode == "mongodb" and tasks_collection is not None:
        try:
            query = {"_id": ObjectId(meeting_id)} if ObjectId.is_valid(meeting_id) else {"_id": meeting_id}
            await tasks_collection.update_one(query, {"$set": updates})
            return True
        except Exception as e:
            print(f"[Database] MongoDB update error: {e}. Falling back to local storage.")
    
    # Local fallback
    store = read_local_storage()
    if meeting_id in store:
        store[meeting_id].update(updates)
        store[meeting_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_local_storage(store)
        return True
    return False

async def get_meeting_doc(meeting_id: str) -> Optional[Dict[str, Any]]:
    global db_mode, tasks_collection
    if db_mode == "mongodb" and tasks_collection is not None:
        try:
            query = {"_id": ObjectId(meeting_id)} if ObjectId.is_valid(meeting_id) else {"_id": meeting_id}
            doc = await tasks_collection.find_one(query)
            if doc:
                doc["_id"] = str(doc["_id"])
                return doc
        except Exception as e:
            print(f"[Database] MongoDB query error: {e}. Checking local storage.")
    
    store = read_local_storage()
    return store.get(str(meeting_id))

async def list_meetings_docs(limit: int = 50) -> List[Dict[str, Any]]:
    global db_mode, tasks_collection
    if db_mode == "mongodb" and tasks_collection is not None:
        try:
            cursor = tasks_collection.find().sort("_id", -1).limit(limit)
            meetings = []
            async for doc in cursor:
                doc["_id"] = str(doc["_id"])
                meetings.append(doc)
            return meetings
        except Exception as e:
            print(f"[Database] MongoDB list error: {e}. Checking local storage.")
    
    store = read_local_storage()
    return list(store.values())[:limit]

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_mode, mongo_client, mongo_db, tasks_collection
    
    print("=" * 60)
    print("Meeting Task Intelligence Backend starting...")
    if llm_api_key:
        print(f"[LLM] Active Provider: {llm_provider_name}")
    else:
        print("[LLM] WARNING: No LLM provider configured.")

    if MONGODB_URL:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo_client = AsyncIOMotorClient(MONGODB_URL, serverSelectionTimeoutMS=2500)
            await mongo_client.admin.command('ping')
            mongo_db = mongo_client.meeting_db
            tasks_collection = mongo_db.factual_tasks
            db_mode = "mongodb"
            print("[Database] Successfully connected to MongoDB Atlas!")
        except Exception as e:
            db_mode = "local"
            init_local_storage()
            print(f"[Database] MongoDB Atlas connection failed ({e}). Using local JSON storage ({LOCAL_STORAGE_FILE}).")
    else:
        db_mode = "local"
        init_local_storage()
        print(f"[Database] MONGODB_URL not set. Using local JSON storage ({LOCAL_STORAGE_FILE}).")
    print("=" * 60)

    yield

    if mongo_client:
        mongo_client.close()
        print("[Database] MongoDB connection closed.")

# ---------------------------------------------------------------------------
# FastAPI Application & CORS
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Meeting Minutes & Task Intelligence API",
    description="Extracts factual action items, infers context and dependencies, and performs graph-based schedule analysis.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Chrome Extensions and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic Schemas for Extension Contract & Endpoints
# ---------------------------------------------------------------------------
class TranscriptEntry(BaseModel):
    timestamp: Optional[str] = None
    text: str
    source: Optional[str] = None
    at: Optional[str] = None

class MeetingMetadata(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = "Untitled meeting"
    participants: Optional[List[str]] = Field(default_factory=list)
    startedAt: Optional[str] = None
    endedAt: Optional[str] = None

class ProcessingOptions(BaseModel):
    requestedOutputs: Optional[List[str]] = Field(default_factory=list)
    source: Optional[str] = None
    version: Optional[str] = None
    final: Optional[bool] = None
    sequence: Optional[int] = None

class MeetingProcessRequest(BaseModel):
    meeting: Optional[MeetingMetadata] = None
    transcript: Optional[Union[List[TranscriptEntry], str]] = None
    transcript_text: Optional[str] = None
    processing: Optional[ProcessingOptions] = None

class TranscriptInput(BaseModel):
    transcript_text: str

# ---------------------------------------------------------------------------
# Helper: Format Transcript from Payload
# ---------------------------------------------------------------------------
def extract_transcript_text(payload: MeetingProcessRequest) -> str:
    # 1. Plain text directly provided
    if payload.transcript_text and payload.transcript_text.strip():
        return payload.transcript_text.strip()
    
    # 2. Transcript is a string
    if isinstance(payload.transcript, str) and payload.transcript.strip():
        return payload.transcript.strip()
    
    # 3. Transcript is a list of entries
    if isinstance(payload.transcript, list) and payload.transcript:
        lines = []
        for entry in payload.transcript:
            speaker = getattr(entry, "source", None)
            timestamp = getattr(entry, "timestamp", None)
            text = entry.text.strip() if hasattr(entry, "text") else str(entry).strip()
            if not text:
                continue
            if timestamp:
                lines.append(f"[{timestamp}] {text}")
            else:
                lines.append(text)
        return "\n".join(lines)
    
    return ""

# ---------------------------------------------------------------------------
# Helper: Run LLM Passes
# ---------------------------------------------------------------------------
ModelT = TypeVar("ModelT", bound=BaseModel)


async def request_structured_completion(
    system_prompt: str,
    user_prompt: str,
    response_model: Type[ModelT],
) -> ModelT:
    if not llm_api_key:
        raise HTTPException(
            status_code=500,
            detail="No LLM provider configured. Please set GEMINI_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY in .env."
        )

    schema = json.dumps(response_model.model_json_schema())
    request_json = {
        "model": llm_model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    "Return only one valid JSON object. Do not return Markdown, commentary, "
                    "code fences, or an explanation. Match this JSON schema exactly:\n"
                    f"{schema}"
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "max_completion_tokens": 8192,
    }
    if llm_provider_name.startswith("Google Gemini") or llm_provider_name.startswith("OpenAI"):
        request_json["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {llm_api_key}"},
            json=request_json,
        )
    if response.is_error:
        try:
            provider_error = response.json()
            detail = provider_error.get("error", provider_error)
        except Exception:
            detail = response.text
        raise HTTPException(
            status_code=response.status_code,
            detail=f"{llm_provider_name} request failed: {detail}",
        )

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("LLM provider returned an unexpected chat-completion response.") from error

    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM provider returned an empty structured response.")

    cleaned_content = content.strip()
    if cleaned_content.startswith("```"):
        lines = cleaned_content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned_content = "\n".join(lines).strip()

    try:
        return response_model.model_validate_json(cleaned_content)
    except Exception as error:
        start = cleaned_content.find("{")
        end = cleaned_content.rfind("}")
        if start >= 0 and end > start:
            return response_model.model_validate_json(cleaned_content[start:end + 1])
        raise error


async def run_factual_extraction(transcript_text: str) -> FactualExtraction:
    return await request_structured_completion(
        system_prompt=(
            "You are a strict factual action-item extractor. Extract only explicit, actionable tasks: "
            "concrete work someone is directly asked or assigned to do, or clearly commits to doing. "
            "Do not infer or invent tasks. Do not turn a topic, suggestion, opinion, status update, "
            "or decision by itself into a task. Exclude greetings, small talk, weather, gossip, and "
            "anecdotes unless someone explicitly states an action they will take. If there are no "
            "qualifying action items, return an empty tasks list. For each task, write a concise, "
            "action-oriented description without first-person phrasing or task-number introductions, "
            "and do not add details that were not stated. Record owners, deadlines, and prerequisites "
            "only when they are explicitly stated in the transcript."
        ),
        user_prompt=transcript_text,
        response_model=FactualExtraction,
    )

async def run_contextual_inference(transcript_text: str, factual_tasks: List[Dict[str, Any]]) -> ContextualInference:
    system_prompt = (
        "You are an AI project manager. You are given a meeting transcript and a strictly factual list of extracted tasks. "
        "Your job is to enrich these tasks by inferring missing context. Assign priorities, flag missing owners/deadlines, "
        "deduce current statuses, and suggest inferred dependencies (tasks that logically must happen first, even if not explicitly stated). "
        "CRITICAL PREREQUISITE RULE: If a task is blocked by an external factor, event, or third-party action that is not tracked as a separate "
        "task ID (e.g., waiting on AWS support, client approvals, or external deliveries), you MUST add a clear descriptive string of this blocker "
        "into the explicit_prerequisites or inferred_prerequisites list (e.g., 'Waiting on AWS support to approve quota'). "
        "Do not leave prerequisites empty if an external blocker exists. Make sure all tasks keep their exact task_id. "
        "Also analyze the conversation for circular dependencies discussed before a resolution: if both tasks explicitly "
        "or clearly stated that each depended on the other's output, add them to initial_circular_dependencies with "
        "from_task_id, to_task_id, and description. If the meeting agrees on an ordering or handoff that resolves it, "
        "add a clear resolution to resolved_dependencies; include task IDs and the agreed execution sequence."
    )
    user_prompt = f"Transcript:\n{transcript_text}\n\nFactual Tasks:\n{factual_tasks}"

    return await request_structured_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=ContextualInference,
    )

# ---------------------------------------------------------------------------
# Core API Routes
# ---------------------------------------------------------------------------
@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "Meeting Minutes & Task Intelligence API is running",
        "llm_provider": llm_provider_name or "Not configured",
        "database_mode": db_mode,
        "endpoints": {
            "process_meeting": "POST /api/meetings/process",
            "extract_factual": "POST /api/v1/extract-factual",
            "infer_context": "POST /api/v1/infer-context/{meeting_id}",
            "list_meetings": "GET /api/meetings",
            "get_meeting": "GET /api/meetings/{meeting_id}",
        }
    }

# Main endpoint consumed by the Chrome Extension
@app.post("/api/meetings/process")
async def process_meeting_from_extension(payload: MeetingProcessRequest):
    """
    Receives meeting payload directly from the Chrome Extension.
    Executes Pass 1 (Factual Extraction), Pass 2 (Contextual Inference),
    and Pass 3 (Deterministic Graph Analysis), then stores and returns the complete result.
    """
    raw_transcript = extract_transcript_text(payload)
    if not raw_transcript:
        raise HTTPException(
            status_code=400,
            detail="Transcript is empty. Speak or provide meeting transcript to process."
        )

    try:
        meeting_meta = payload.meeting.model_dump() if payload.meeting else {
            "title": "Untitled meeting",
            "participants": [],
            "startedAt": None,
            "endedAt": datetime.now(timezone.utc).isoformat(),
        }

        # Pass 1: Strict Factual Extraction
        extracted_factual = await run_factual_extraction(raw_transcript)
        factual_tasks_dict = [t.model_dump() for t in extracted_factual.tasks]

        # Initial Document Save
        meeting_doc = {
            "meeting_title": meeting_meta.get("title") or extracted_factual.meeting_title,
            "date": extracted_factual.date or meeting_meta.get("startedAt") or datetime.now(timezone.utc).isoformat(),
            "participants": meeting_meta.get("participants", []),
            "raw_transcript": raw_transcript,
            "tasks": factual_tasks_dict,
            "processing_metadata": payload.processing.model_dump() if payload.processing else None,
            "status": "factual_extracted",
        }
        meeting_id = await save_meeting_doc(meeting_doc)

        # Pass 2: Contextual Inference
        enriched_data = await run_contextual_inference(raw_transcript, factual_tasks_dict)
        enriched_tasks_dict = [task.model_dump() for task in enriched_data.enriched_tasks]

        # Pass 3: Deterministic Graph Validation
        graph_analysis = analyze_task_graph(
            enriched_tasks_dict,
            enriched_data.model_dump(),
        )

        # Update Document in Database
        await update_meeting_doc(meeting_id, {
            "enriched_tasks": enriched_tasks_dict,
            "graph_analysis": graph_analysis,
            "status": "completed",
        })

        return {
            "status": "success",
            "meeting_id": meeting_id,
            "meeting_title": meeting_doc["meeting_title"],
            "tasks": enriched_tasks_dict,
            "factual_tasks": factual_tasks_dict,
            "graph_analysis": graph_analysis,
            "total_tasks": len(enriched_tasks_dict),
            "bottlenecks_count": len(graph_analysis.get("bottlenecks", [])),
            "is_feasible": graph_analysis.get("is_feasible", True),
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Error] Processing failed: {type(e).__name__}: {repr(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Modular Pass 1 Endpoint
@app.post("/api/v1/extract-factual")
async def extract_factual_pass(payload: TranscriptInput):
    try:
        extracted_data = await run_factual_extraction(payload.transcript_text)
        meeting_doc = extracted_data.model_dump()
        meeting_doc["raw_transcript"] = payload.transcript_text
        meeting_doc["status"] = "factual_extracted"

        meeting_id = await save_meeting_doc(meeting_doc)
        return {
            "status": "success",
            "meeting_id": meeting_id,
            "data": extracted_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Modular Pass 2 Endpoint
@app.post("/api/v1/infer-context/{meeting_id}")
async def infer_context_pass(meeting_id: str):
    try:
        meeting_doc = await get_meeting_doc(meeting_id)
        if not meeting_doc:
            raise HTTPException(status_code=404, detail="Meeting not found")

        raw_transcript = meeting_doc.get("raw_transcript", "")
        factual_tasks = meeting_doc.get("tasks", [])

        enriched_data = await run_contextual_inference(raw_transcript, factual_tasks)
        enriched_tasks_dict = [task.model_dump() for task in enriched_data.enriched_tasks]

        graph_analysis = analyze_task_graph(
            enriched_tasks_dict,
            enriched_data.model_dump(),
        )

        await update_meeting_doc(meeting_id, {
            "enriched_tasks": enriched_tasks_dict,
            "graph_analysis": graph_analysis,
            "status": "completed",
        })

        return {
            "status": "success",
            "message": "Contextual inference and graph validation complete",
            "data": {
                "tasks": enriched_tasks_dict,
                "graph_analysis": graph_analysis,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Meetings list and retrieval
@app.get("/api/meetings")
async def list_meetings():
    meetings = await list_meetings_docs(limit=50)
    return {"meetings": meetings}

@app.get("/api/meetings/{meeting_id}")
async def get_meeting(meeting_id: str):
    meeting = await get_meeting_doc(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting uvicorn server on http://{host}:{port}...")
    uvicorn.run("main:app", host=host, port=port, reload=True)
