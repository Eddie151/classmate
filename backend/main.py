"""ClassMate FastAPI application."""
import asyncio
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

try:
    with open(_BASE / "supported_courses.json") as f:
        _SUPPORTED: dict[str, list[dict]] = json.load(f)
    _SUPPORTED_SET: dict[str, set[str]] = {
        slug: {c["course_code"] for c in courses}
        for slug, courses in _SUPPORTED.items()
    }
    total_supported = sum(len(v) for v in _SUPPORTED.values())
    logger.info("Supported courses loaded: %d total", total_supported)
except FileNotFoundError:
    logger.warning("supported_courses.json not found — all course requests will pass through ungated")
    _SUPPORTED = {}
    _SUPPORTED_SET = {}
except json.JSONDecodeError as e:
    logger.warning("supported_courses.json invalid JSON: %s — gating disabled", e)
    _SUPPORTED = {}
    _SUPPORTED_SET = {}

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

_PREFIX_ALIASES: dict[str, str] = {
    "ITSC": "ITCS",
    "ITCS": "ITSC",
}


def _no_data_response(course_code: str, school: str) -> dict:
    return {
        "course_code": course_code,
        "school":      school,
        "professors":  [],
        "source":      "no_data",
        "message":     "No reviews available for this course yet. Try a similar course or check back next semester.",
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


@app.get("/supported_courses/{school}")
async def get_supported_courses(school: str) -> dict:
    """Return supported courses grouped by department prefix for the browse section."""
    if school not in _SCHOOLS_BY_SLUG:
        raise HTTPException(status_code=404, detail=f"School not found: {school!r}")

    courses = _SUPPORTED.get(school, [])

    # Group by department prefix (first token of course code)
    groups: dict[str, list[dict]] = {}
    for c in courses:
        prefix = c["course_code"].split()[0]
        groups.setdefault(prefix, []).append({
            "code":  c["course_code"],
            "title": c["course_name"],
        })

    # Sort within each group by code, and sort groups alphabetically
    grouped = [
        {"prefix": prefix, "courses": sorted(courses, key=lambda x: x["code"])}
        for prefix, courses in sorted(groups.items())
    ]

    return {
        "school":  school,
        "total":   len(courses),
        "groups":  grouped,
    }


@app.post("/resolve")
def resolve(body: ResolveRequest) -> dict:
    if body.school not in _SCHOOLS_BY_SLUG:
        raise HTTPException(status_code=404, detail=f"School not found: {body.school!r}")
    if not body.input or len(body.input.strip()) < 2:
        return {"status": "no_match", "stage": 0, "reason": "empty input"}
    try:
        return course_resolver.resolve_course(body.school, body.input)
    except Exception as e:
        logger.warning("resolve_course error for %r: %s", body.input, e)
        return {"status": "no_match", "stage": -1,
                "reason": "resolver temporarily unavailable, try entering the exact course code"}


@app.get("/course/{school}/{code}")
async def get_course_insights(school: str, code: str) -> dict:
    if school not in _SCHOOLS_BY_SLUG:
        raise HTTPException(status_code=404, detail=f"School not found: {school!r}")

    resolved = course_resolver.resolve_course(school, code)
    if resolved["status"] == "ambiguous":
        return resolved
    if resolved["status"] == "no_match":
        raise HTTPException(status_code=404, detail=f"Course not found: {code!r}")

    course_code = resolved["code"]

    # Gate: only proceed for courses we have real data for.
    # Check both the resolved code and its prefix alias (e.g. ITSC 1212 ↔ ITCS 1212).
    _prefix     = course_code.split()[0]
    _alias_code = (_PREFIX_ALIASES[_prefix] + course_code[len(_prefix):]) if _prefix in _PREFIX_ALIASES else None
    if _SUPPORTED_SET and course_code not in _SUPPORTED_SET.get(school, set()) and (
        _alias_code is None or _alias_code not in _SUPPORTED_SET.get(school, set())
    ):
        prefix = _prefix
        suggestions = [
            {"code": c["course_code"], "title": c["course_name"]}
            for c in _SUPPORTED.get(school, [])
            if c["course_code"].startswith(prefix)
        ][:5]
        if not suggestions:
            suggestions = sorted(
                _SUPPORTED.get(school, []),
                key=lambda c: c["review_count"],
                reverse=True,
            )[:5]
            suggestions = [{"code": c["course_code"], "title": c["course_name"]} for c in suggestions]
        return {
            "source":      "unsupported",
            "course_code": course_code,
            "message":     (
                f"We don't have data for {course_code} yet. "
                "ClassMate currently covers 760 courses across UNCC, UNC, and NC State."
            ),
            "suggestions": suggestions,
        }

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

    # Alias prefix cache lookup (e.g. ITSC 1600 ↔ ITCS 1600)
    if not professors:
        prefix = course_code.split()[0]
        alias_prefix = _PREFIX_ALIASES.get(prefix)
        if alias_prefix:
            alias_code = alias_prefix + course_code[len(prefix):]
            professors = [p for p in dept_faculty if alias_code in p.get("courses_taught", [])]
            if professors:
                logger.info("Alias cache hit (%s->%s): %d professor(s) for %s / %s",
                            prefix, alias_prefix, len(professors), school, course_code)

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
        return _no_data_response(course_code, school)

    seen_ids = set()
    deduped = []
    for p in professors:
        pid = p.get("rmp_id") or p.get("id")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            deduped.append(p)
    professors = deduped

    async def _fetch_one(prof: dict) -> dict | None:
        try:
            reddit_posts = await asyncio.to_thread(
                reddit_client.get_professor_posts,
                school_data["subreddit"], prof["name"], course_code, limit=10,
            )
            insight = await asyncio.to_thread(
                insights.generate_insights,
                professor_name=prof["name"],
                course_code=course_code,
                reddit_posts=reddit_posts,
                rmp_reviews=prof.get("reviews", []),
            )
            return {
                "name":        prof["name"],
                "rating":      prof.get("rating"),
                "num_ratings": prof.get("num_ratings", 0),
                "insights":    insight,
                "_posts":      reddit_posts,
            }
        except Exception as e:
            logger.warning("generate_insights failed for %r: %s", prof["name"], e)
            return None

    gathered = await asyncio.gather(*[_fetch_one(p) for p in professors[:3]])
    professor_results = [r for r in gathered if r is not None]
    total_reddit_posts = sum(len(r.pop("_posts")) for r in professor_results)

    if not professor_results:
        return _no_data_response(course_code, school)

    return {
        "course_code":       course_code,
        "school":            school,
        "professors":        professor_results,
        "source":            "real",
        "reddit_post_count": total_reddit_posts,
    }
