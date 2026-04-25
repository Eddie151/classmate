"""ClassMate FastAPI application."""
import json
import logging
import pathlib
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.requests import Request

from backend import course_resolver
from backend import insights

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("classmate")

_BASE = pathlib.Path(__file__).parent

try:
    with open(_BASE / "schools.json") as f:
        _SCHOOLS: list[dict] = json.load(f)
except FileNotFoundError:
    raise RuntimeError("schools.json not found — cannot start.")
except json.JSONDecodeError as e:
    raise RuntimeError(f"schools.json is invalid JSON: {e}")

try:
    with open(_BASE / "courses.json") as f:
        _COURSES: dict[str, list[dict]] = json.load(f)
except FileNotFoundError:
    raise RuntimeError("courses.json not found — cannot start.")
except json.JSONDecodeError as e:
    raise RuntimeError(f"courses.json is invalid JSON: {e}")

_SCHOOLS_BY_SLUG: dict[str, dict] = {s["slug"]: s for s in _SCHOOLS}

# ---------- App ----------

app = FastAPI(title="ClassMate API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    logger.info("%s %s -> %d (%.0fms)", request.method, request.url.path, response.status_code, ms)
    return response


# ---------- Pydantic models ----------

class HealthResponse(BaseModel):
    status: str
    service: str


class SchoolInfo(BaseModel):
    slug: str
    display_name: str
    subreddit: str
    primary_color: str


class ResolveRequest(BaseModel):
    school: str
    input: str


# ---------- Routes ----------

@app.get("/", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="ClassMate API")


@app.get("/schools", response_model=list[SchoolInfo])
async def get_schools() -> list[SchoolInfo]:
    return [
        SchoolInfo(
            slug=s["slug"],
            display_name=s["display_name"],
            subreddit=s["subreddit"],
            primary_color=s["primary_color"],
        )
        for s in _SCHOOLS
    ]


@app.get("/courses/{school}")
async def get_courses(school: str) -> list[dict]:
    if school not in _SCHOOLS_BY_SLUG:
        raise HTTPException(status_code=404, detail=f"School not found: {school!r}")
    return [
        {"code": c["code"], "title": c["title"]}
        for c in _COURSES.get(school, [])
    ]


@app.post("/resolve")
def resolve(body: ResolveRequest) -> dict:
    if body.school not in _SCHOOLS_BY_SLUG:
        raise HTTPException(status_code=404, detail=f"School not found: {body.school!r}")
    return course_resolver.resolve_course(body.school, body.input)


@app.get("/course/{school}/{code}")
def get_course_insights(school: str, code: str) -> dict:
    if school not in _SCHOOLS_BY_SLUG:
        raise HTTPException(status_code=404, detail=f"School not found: {school!r}")

    resolved = course_resolver.resolve_course(school, code)

    if resolved["status"] == "ambiguous":
        return resolved
    if resolved["status"] == "no_match":
        raise HTTPException(status_code=404, detail=f"Course not found: {code!r}")

    try:
        return insights.generate_insights(
            professor_name=insights.MOCK_DATA["professor_name"],
            course_code=resolved["code"],
            reddit_posts=insights.MOCK_DATA["reddit_posts"],
            rmp_reviews=insights.MOCK_DATA["rmp_reviews"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
