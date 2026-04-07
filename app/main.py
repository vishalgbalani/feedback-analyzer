import os
import json
import time
import uuid
import asyncio
import traceback
from collections import defaultdict
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.csv_parser import parse_csv
from app.review_fetcher import search_google_play_apps, fetch_reviews_for_app
from app.pipeline import run_analysis, results_cache

app = FastAPI(title="Customer Feedback Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting: 5 requests per IP per day
rate_limit_store: dict = defaultdict(list)
DAILY_LIMIT = 5

LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}



def check_rate_limit(ip: str) -> bool:
    if ip in LOCALHOST_IPS:
        return True
    now = time.time()
    day_ago = now - 86400
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if t > day_ago]
    if len(rate_limit_store[ip]) >= DAILY_LIMIT:
        return False
    rate_limit_store[ip].append(now)
    return True


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
    "Content-Type": "text/event-stream",
}


async def sse_generator(feedback_items, data_source, source_labels=None):
    session_id = uuid.uuid4().hex
    try:
        async for event in run_analysis(feedback_items, data_source, source_labels, session_id=session_id):
            event_type = event.get("event", "status")
            data = event.get("data", "")
            yield f"event: {event_type}\ndata: {data}\n\n"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"[SSE ERROR] {error_msg}\n{traceback.format_exc()}")
        yield f"event: error\ndata: {error_msg}\n\n"


@app.get("/health")
async def health():
    return {"status": "ok"}



@app.post("/analyze")
async def analyze(request: Request):
    ip = get_client_ip(request)
    if not check_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail="Daily limit reached (5 analyses per day). This is a free demo — thanks for trying it!",
        )

    body = await request.json()
    feedback_text = body.get("feedback_text", "")
    if not feedback_text.strip():
        raise HTTPException(status_code=400, detail="No feedback text provided.")

    lines = [line.strip() for line in feedback_text.strip().split("\n") if line.strip()]
    feedback_items = [{"text": line} for line in lines]
    return StreamingResponse(
        sse_generator(feedback_items, "Pasted text"),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/analyze-csv")
async def analyze_csv(request: Request, file: UploadFile = File(...)):
    ip = get_client_ip(request)
    if not check_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail="Daily limit reached (5 analyses per day). This is a free demo — thanks for trying it!",
        )

    if file.size and file.size > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")

    file_bytes = await file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")

    try:
        feedback_items, col_info = parse_csv(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return StreamingResponse(
        sse_generator(feedback_items, "CSV upload"),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/parse-csv")
async def parse_csv_preview(file: UploadFile = File(...)):
    """Parse CSV and return column detection info without running analysis."""
    if file.size and file.size > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")

    file_bytes = await file.read()
    try:
        feedback_items, col_info = parse_csv(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "columns": col_info,
        "item_count": len(feedback_items),
        "preview": feedback_items[:3],
    }


@app.post("/search-apps")
async def search_apps(request: Request):
    body = await request.json()
    app_name = body.get("app_name", "")
    if not app_name.strip():
        raise HTTPException(status_code=400, detail="No app name provided.")

    result = search_google_play_apps(app_name.strip())
    if not result["best"] and not result["other"]:
        raise HTTPException(
            status_code=404,
            detail=f"No apps found matching '{app_name}'. Try the exact app name.",
        )

    return result


@app.post("/fetch-reviews")
async def fetch_reviews(request: Request):
    body = await request.json()
    app_id = body.get("app_id", "")
    app_name = body.get("app_name", "")
    if not app_id.strip():
        raise HTTPException(status_code=400, detail="No app ID provided.")

    count = min(int(body.get("count", 50)), 200)
    result = fetch_reviews_for_app(app_id.strip(), count=count)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    result["app_name"] = app_name
    return result


@app.post("/analyze-reviews")
async def analyze_reviews(request: Request):
    ip = get_client_ip(request)
    if not check_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail="Daily limit reached (5 analyses per day). This is a free demo — thanks for trying it!",
        )

    body = await request.json()
    reviews_data = body.get("reviews", [])
    app_name = body.get("app_name", "Google Play App")

    if not reviews_data:
        raise HTTPException(status_code=400, detail="No reviews provided.")

    feedback_items = [
        {
            "text": r.get("text", ""),
            "rating": r.get("rating"),
            "date": r.get("date"),
            "source": "Google Play",
        }
        for r in reviews_data
    ]

    return StreamingResponse(
        sse_generator(feedback_items, f"Google Play: {app_name}"),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/analyze-combined")
async def analyze_combined(request: Request):
    ip = get_client_ip(request)
    if not check_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail="Daily limit reached (5 analyses per day). This is a free demo — thanks for trying it!",
        )

    body = await request.json()
    gp_reviews = body.get("google_play_reviews", [])
    additional_feedback = body.get("additional_feedback", "")
    app_name = body.get("app_name", "Google Play App")

    feedback_items = []
    source_labels = {}

    for r in gp_reviews:
        idx = len(feedback_items)
        feedback_items.append({
            "text": r.get("text", ""),
            "rating": r.get("rating"),
            "date": r.get("date"),
            "source": "Google Play",
        })
        source_labels[idx] = "Google Play"

    if additional_feedback.strip():
        lines = [l.strip() for l in additional_feedback.strip().split("\n") if l.strip()]
        for line in lines:
            idx = len(feedback_items)
            feedback_items.append({"text": line, "source": "User Provided"})
            source_labels[idx] = "User Provided"

    if not feedback_items:
        raise HTTPException(status_code=400, detail="No feedback provided.")

    data_source = f"Combined: Google Play ({app_name}) + User Provided"
    return StreamingResponse(
        sse_generator(feedback_items, data_source, source_labels),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/analyze-combined-csv")
async def analyze_combined_csv(
    request: Request,
    file: UploadFile = File(...),
    google_play_reviews: str = Form("[]"),
    app_name: str = Form("Google Play App"),
):
    ip = get_client_ip(request)
    if not check_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail="Daily limit reached (5 analyses per day). This is a free demo — thanks for trying it!",
        )

    file_bytes = await file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")

    try:
        csv_items, _ = parse_csv(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    gp_reviews = json.loads(google_play_reviews)

    feedback_items = []
    source_labels = {}

    for r in gp_reviews:
        idx = len(feedback_items)
        feedback_items.append({
            "text": r.get("text", ""),
            "rating": r.get("rating"),
            "date": r.get("date"),
            "source": "Google Play",
        })
        source_labels[idx] = "Google Play"

    for item in csv_items:
        idx = len(feedback_items)
        item["source"] = "User Provided"
        feedback_items.append(item)
        source_labels[idx] = "User Provided"

    data_source = f"Combined: Google Play ({app_name}) + CSV Upload"
    return StreamingResponse(
        sse_generator(feedback_items, data_source, source_labels),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.get("/result/{session_id}")
async def get_result(session_id: str):
    if session_id not in results_cache:
        raise HTTPException(status_code=404, detail="Result not found or not ready yet.")
    return JSONResponse(content=results_cache.pop(session_id))


@app.get("/")
async def serve_frontend():
    return FileResponse("index.html")
