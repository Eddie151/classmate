"""Offline script: scrapes department faculty directories and builds faculty_cache.json.

Run with:  python -m backend.faculty_scraper
Writes to: backend/faculty_cache.json

Expected runtime: ~3-5 min for UNCC CS (70 professors × 2 RMP calls + rate-limit delay).
Re-run once per semester to refresh the cache.
"""
import json
import logging
import pathlib
import re
import time

import requests
from bs4 import BeautifulSoup

from backend import rmp_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("faculty_scraper")

_BASE       = pathlib.Path(__file__).parent
_CACHE_PATH = _BASE / "faculty_cache.json"
_RMP_DELAY  = 0.5  # seconds between RMP calls

# One entry per (school, department) to scrape.
# Add more schools/depts here as ClassMate expands.
_SOURCES = [
    {
        "school_slug":     "uncc",
        "rmp_school_name": "University of North Carolina at Charlotte",
        "department":      "Computer Science",
        "directory_url":   "https://cci.charlotte.edu/directory/faculty/",
    },
]

_NAV_NOISE = {
    "Dean's Advisory Board", "Faculty", "Ph.D. Students", "Contact Us",
    "Emeritus Faculty", "Adjunct Faculty", "Previous", "Next",
    "Map and Directions", "APPLAUSE for Faculty. SUPPORT for Staff.",
}

# Acceptable RMP department strings for each expected department label.
_DEPT_ALIASES: dict[str, list[str]] = {
    "Computer Science": [
        "Computer Science", "Information Technology", "Computer Information Systems",
        "Software Engineering", "ITCS", "ITIS", "Computing", "Information Systems",
    ],
    "Mathematics": ["Mathematics", "Math", "Statistics", "Applied Mathematics"],
}

# Canonical prefix for course codes that have multiple spellings on RMP.
_PREFIX_ALIASES: dict[str, str] = {
    "ITSC": "ITCS",
    "CSCI": "CSC",
}

_NAME_RE = re.compile(r"^[A-Z][a-zA-Z'\-\.]+$")


def _is_valid_faculty_name(text: str) -> bool:
    """Return True if text looks like a real person's name (2+ capitalized words, 5-40 chars)."""
    if not (5 <= len(text) <= 40):
        return False
    words = text.split()
    if len(words) < 2:
        return False
    # Every word must start with a capital letter; allow hyphenated/abbreviated names
    return all(_NAME_RE.match(w) for w in words)


def _dept_matches(rmp_dept: str, expected_dept: str) -> bool:
    """Return True if rmp_dept is an acceptable alias for expected_dept."""
    aliases = _DEPT_ALIASES.get(expected_dept, [expected_dept])
    return any(alias.lower() in rmp_dept.lower() for alias in aliases)


def _scrape_cci_names(directory_url: str) -> list[str]:
    """Scrape all faculty names from a paginated CCI directory. Returns unique names in listing order."""
    seen:  set[str]  = set()
    names: list[str] = []
    url = directory_url

    while url:
        logger.info("Fetching directory page: %s", url)
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        except requests.exceptions.RequestException as e:
            logger.warning("Request failed: %s", e)
            break

        if not resp.ok:
            logger.warning("HTTP %d — stopping pagination", resp.status_code)
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.find_all("a", href=re.compile(r"/directory/[a-z][a-z\-]+/$")):
            text = a.get_text(strip=True)
            if _is_valid_faculty_name(text) and text not in seen:
                seen.add(text)
                names.append(text)

        nxt = soup.find("a", string=re.compile(r"Next"))
        url  = nxt["href"] if nxt else None

    logger.info("Scraped %d unique faculty names", len(names))
    return names


def _normalize_course(class_name: str) -> str | None:
    """Normalize 'ITCS1212' or 'itcs 1212' → 'ITCS 1212'. Returns None if not a valid code."""
    m = re.match(r"^([A-Za-z]{2,6})\s*(\d{3,4}[A-Za-z]?)$", class_name.strip())
    if not m:
        return None
    prefix = _PREFIX_ALIASES.get(m.group(1).upper(), m.group(1).upper())
    return f"{prefix} {m.group(2).upper()}"


def _build_record(prof: dict, reviews: list[dict]) -> dict:
    """Build a cache record from an RMP professor dict + their full review list."""
    recent_reviews = [r for r in reviews if r.get("date", "")[:4] >= "2023"]

    # courses_taught drawn from all reviews (not just recent) to maximise coverage
    courses_taught = sorted({
        code
        for r in reviews
        if (code := _normalize_course(r.get("class_name", "")))
    })

    return {
        "name":             prof["name"],
        "rmp_id":           prof["id"],
        "rating":           prof.get("rating"),
        "difficulty":       prof.get("difficulty"),
        "num_ratings":      prof.get("num_ratings", 0),
        "department":       prof.get("department", ""),
        "would_take_again": prof.get("would_take_again"),
        "courses_taught":   courses_taught,
        "reviews":          recent_reviews,
    }


def build_cache() -> None:
    # Preserve existing cache entries for schools/depts we're not re-scraping
    cache: dict = {}
    if _CACHE_PATH.exists():
        try:
            with open(_CACHE_PATH) as f:
                cache = json.load(f)
            logger.info("Loaded existing cache from %s", _CACHE_PATH)
        except (json.JSONDecodeError, OSError):
            logger.warning("Existing cache unreadable — starting fresh")

    for source in _SOURCES:
        slug     = source["school_slug"]
        rmp_name = source["rmp_school_name"]
        dept     = source["department"]
        dir_url  = source["directory_url"]

        logger.info("=== %s / %s ===", slug, dept)

        try:
            school_id = rmp_client.get_rmp_school_id(rmp_name)
            logger.info("RMP school ID: %s", school_id)
        except ValueError as e:
            logger.error("RMP school lookup failed for %r: %s", rmp_name, e)
            continue

        names = _scrape_cci_names(dir_url)

        records:    list[dict] = []
        found      = 0
        not_found  = 0
        no_ratings = 0
        wrong_dept = 0

        for i, name in enumerate(names, 1):
            logger.info("[%d/%d] %s", i, len(names), name)

            prof = rmp_client.search_professor(school_id, name)
            time.sleep(_RMP_DELAY)

            if not prof:
                logger.info("  → not found on RMP")
                not_found += 1
                continue

            if prof["num_ratings"] == 0:
                logger.info("  → found but 0 ratings, skipping")
                no_ratings += 1
                continue

            if not _dept_matches(prof["department"], dept):
                logger.warning(
                    "  → dept mismatch: RMP says %r, expected %r — skipping",
                    prof["department"], dept,
                )
                wrong_dept += 1
                continue

            logger.info(
                "  → %s | %d ratings | dept: %s",
                prof["name"], prof["num_ratings"], prof["department"],
            )

            reviews = rmp_client.get_professor_reviews(prof["id"])
            time.sleep(_RMP_DELAY)

            record = _build_record(prof, reviews)
            records.append(record)
            found += 1
            logger.info(
                "  → courses: %s | recent reviews: %d",
                record["courses_taught"] or "(none parsed)",
                len(record["reviews"]),
            )

        records.sort(key=lambda p: p["num_ratings"] or 0, reverse=True)
        cache.setdefault(slug, {})[dept] = records

        logger.info(
            "Done: %d stored, %d not on RMP, %d zero-ratings, %d wrong dept",
            found, not_found, no_ratings, wrong_dept,
        )

    with open(_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
    logger.info("Cache written → %s", _CACHE_PATH)


if __name__ == "__main__":
    build_cache()
