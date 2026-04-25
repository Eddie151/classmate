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
from backend import professor_matcher
from backend import reddit_client
from backend import rmp_client

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

try:
    with open(_BASE / "faculty_cache.json") as f:
        _FACULTY_CACHE: dict = json.load(f)
    logger.info("Faculty cache loaded: %d schools", len(_FACULTY_CACHE))
except FileNotFoundError:
    logger.warning("faculty_cache.json not found — cache lookups will miss. Run: python -m backend.faculty_scraper")
    _FACULTY_CACHE = {}
except json.JSONDecodeError as e:
    logger.warning("faculty_cache.json invalid JSON: %s — using empty cache", e)
    _FACULTY_CACHE = {}

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


# ---------- Helpers ----------

def _mock_response(course_code: str, school: str) -> dict:
    try:
        mock_insight = insights.generate_insights(
            professor_name=insights.MOCK_DATA["professor_name"],
            course_code=course_code,
            reddit_posts=insights.MOCK_DATA["reddit_posts"],
            rmp_reviews=insights.MOCK_DATA["rmp_reviews"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "course_code":       course_code,
        "school":            school,
        "professors": [{
            "name":        insights.MOCK_DATA["professor_name"],
            "rating":      None,
            "num_ratings": 0,
            "insights":    mock_insight,
        }],
        "source":            "mock",
        "reddit_post_count": 0,
    }


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

    course_code = resolved["code"]
    school_data = _SCHOOLS_BY_SLUG[school]
    department  = rmp_client.get_department_for_code(course_code)

    # Primary: faculty cache lookup (instant — no live RMP calls)
    professors: list[dict] = []
    dept_faculty = _FACULTY_CACHE.get(school, {}).get(department, [])
    professors   = [p for p in dept_faculty if course_code in p.get("courses_taught", [])]
    if professors:
        logger.info("Cache hit: %d professor(s) for %s / %s", len(professors), school, course_code)
    else:
        logger.info("Cache miss for %s / %s — trying live RMP", school, course_code)

    # First fallback: live RMP course search
    if not professors:
        try:
            professors = rmp_client.get_professors_for_course(
                school_data["rmp_school_name"], course_code, department, limit=5,
            )
        except Exception as e:
            logger.warning("RMP course search failed: %s", e)

    # Second fallback: Reddit name extraction + RMP matcher
    if not professors:
        try:
            reddit_posts = reddit_client.get_professor_posts(
                school_data["subreddit"], course_code, course_code, limit=15,
            )
            professors = professor_matcher.match_professors(school, reddit_posts, course_code)
        except Exception as e:
            logger.warning("Reddit/matcher fallback failed: %s", e)

    if not professors:
        return _mock_response(course_code, school)

    professor_results: list[dict] = []
    deadline = time.perf_counter() + 15.0
    total_reddit_posts = 0

    for prof in professors[:3]:
        if time.perf_counter() > deadline:
            logger.warning("15s budget exceeded — skipping remaining professors")
            break
        try:
            reddit_posts = reddit_client.get_professor_posts(
                school_data["subreddit"], prof["name"], course_code, limit=10,
            )
            total_reddit_posts += len(reddit_posts)
            insight = insights.generate_insights(
                professor_name=prof["name"],
                course_code=course_code,
                reddit_posts=reddit_posts,
                rmp_reviews=prof.get("reviews", []),
            )
            professor_results.append({
                "name":        prof["name"],
                "rating":      prof.get("rating"),
                "num_ratings": prof.get("num_ratings", 0),
                "insights":    insight,
            })
        except Exception as e:
            logger.warning("generate_insights failed for %r: %s", prof["name"], e)

    if not professor_results:
        return _mock_response(course_code, school)

    return {
        "course_code":       course_code,
        "school":            school,
        "professors":        professor_results,
        "source":            "real",
        "reddit_post_count": total_reddit_posts,
    }
