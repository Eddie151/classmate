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

try:
    from playwright.sync_api import sync_playwright as _sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

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
_UNCC = {
    "school_slug":     "uncc",
    "rmp_school_name": "University of North Carolina at Charlotte",
}

_UNC = {
    "school_slug":     "unc",
    "rmp_school_name": "University of North Carolina at Chapel Hill",
    "scraper":         "playwright",
}

_SOURCES = [
    {
        **_UNCC,
        "department":       "Computer Science",
        "directory_url":    "https://cci.charlotte.edu/directory/faculty/",
        "rmp_dept_aliases": ["Computer Science", "Information Technology", "Computer Information Systems", "Software Engineering", "Computing"],
    },
    {
        **_UNCC,
        "department":       "Mathematics",
        "directory_url":    "https://math.charlotte.edu/people/",
        "rmp_dept_aliases": ["Mathematics", "Math", "Applied Mathematics", "Statistics"],
    },
    {
        **_UNCC,
        "department":       "Chemistry",
        "directory_url":    "https://chemistry.charlotte.edu/people/",
        "rmp_dept_aliases": ["Chemistry", "Chemical", "Biochemistry", "Organic Chemistry", "Physical Chemistry", "Analytical Chemistry", "Chemistry and Biochemistry"],
    },
    {
        **_UNCC,
        "department":       "Biology",
        "directory_url":    "https://biology.charlotte.edu/directory/faculty/",
        "rmp_dept_aliases": ["Biology", "Biological Sciences", "Bioinformatics", "Biophysics"],
    },
    {
        **_UNCC,
        "department":       "Physics",
        "directory_url":    "https://physics.charlotte.edu/about-us/faculty-and-staff/faculty/",
        "rmp_dept_aliases": ["Physics", "Optical Science", "Engineering Physics"],
    },
    {
        **_UNCC,
        "department":       "Mechanical Engineering",
        "directory_url":    "https://mees.charlotte.edu/directory/faculty/",
        "rmp_dept_aliases": ["Mechanical Engineering", "Engineering Science", "Energy", "Earth Sciences", "Environmental Science"],
    },
    {
        **_UNCC,
        "department":       "Electrical Engineering",
        "directory_url":    "https://ece.charlotte.edu/directory/faculty/",
        "rmp_dept_aliases": ["Electrical Engineering", "Electrical & Computer Engineering", "Computer Engineering", "ECE"],
    },
    {
        **_UNCC,
        "department":       "Psychology",
        "directory_url":    "https://psych.charlotte.edu/people/",
        "rmp_dept_aliases": ["Psychology", "Psychological Science", "Psychological Sciences", "Behavioral Science", "Cognitive Science", "Neuroscience"],
    },
    {
        **_UNCC,
        "department":       "Sociology",
        "directory_url":    "https://sociology.charlotte.edu/people/",
        "rmp_dept_aliases": ["Sociology", "Social Science", "Social Sciences", "Anthropology", "Criminology"],
    },
    {
        **_UNCC,
        "department":       "History",
        "directory_url":    "https://history.charlotte.edu/people/",
        "rmp_dept_aliases": ["History", "Historical Studies", "Humanities", "Social Studies"],
    },
    {
        **_UNCC,
        "department":       "Political Science",
        "directory_url":    "https://politicalscience.charlotte.edu/people/",
        "rmp_dept_aliases": ["Political Science", "Political Studies", "Government", "Public Policy", "International Studies"],
    },
    {
        **_UNCC,
        "department":       "Philosophy",
        "directory_url":    "https://philosophy.charlotte.edu/people/",
        "rmp_dept_aliases": ["Philosophy", "Religion", "Ethics"],
    },
    {
        **_UNCC,
        "department":       "Criminal Justice",
        "directory_url":    "https://criminaljustice.charlotte.edu/people/",
        "rmp_dept_aliases": ["Criminal Justice", "Criminology", "Security Studies"],
    },
    {
        **_UNCC,
        "department":       "Languages",
        "directory_url":    "https://languages.charlotte.edu/people/",
        "rmp_dept_aliases": ["Languages", "Modern Languages", "Spanish", "French", "German", "Chinese", "Japanese", "Arabic", "Foreign Language"],
    },
    # ---- UNC Chapel Hill (Playwright required — dept sites block plain requests) ----
    {
        **_UNC,
        "department":       "Computer Science",
        "directory_url":    "https://cs.unc.edu/about/people?wpv-designation=faculty",
        "rmp_dept_aliases": ["Computer Science", "Information Science", "Computer Engineering", "Computing"],
    },
    {
        **_UNC,
        "department":       "Mathematics",
        "directory_url":    "https://math.unc.edu/faculty/",
        "rmp_dept_aliases": ["Mathematics", "Math", "Statistics", "Applied Mathematics"],
        # /faculty-member/<slug>/ links with "Last, First" text — _flip_last_first handles conversion
    },
    {
        **_UNC,
        "department":       "Statistics",
        "directory_url":    "https://stor.unc.edu/people/faculty/",
        "rmp_dept_aliases": ["Statistics", "Operations Research", "Biostatistics", "Data Science"],
        # /faculty-member/<slug>/ links with "Last, First" text
    },
    {
        **_UNC,
        "department":       "Biology",
        "directory_url":    "https://bio.unc.edu/people/faculty/",
        "rmp_dept_aliases": ["Biology", "Biological Sciences", "Biophysics"],
        "name_selector":    ".col-sm-10",  # names in card text, not link text
    },
    {
        **_UNC,
        "department":       "Chemistry",
        "directory_url":    "https://chem.unc.edu/faculty/",
        "rmp_dept_aliases": ["Chemistry", "Chemical Biology", "Biochemistry", "Organic Chemistry"],
        # /faculty/<slug>/ links; one link per card has "Last, First" text
    },
    {
        **_UNC,
        "department":       "Physics",
        "directory_url":    "https://physics.unc.edu/people-pages/faculty/",
        "rmp_dept_aliases": ["Physics", "Astrophysics", "Optical Science", "Engineering Physics"],
        # /people/<slug>/ links with ALL-CAPS "LAST, FIRST" text — _flip_last_first handles
    },
    {
        **_UNC,
        "department":       "Economics",
        "directory_url":    "https://econ.unc.edu/people/",
        "rmp_dept_aliases": ["Economics", "Finance", "Business Economics", "Political Economy"],
        # /people/<slug>/ links with "Last, First" text
    },
]

_NAV_NOISE = {
    "Dean's Advisory Board", "Faculty", "Ph.D. Students", "Contact Us",
    "Emeritus Faculty", "Adjunct Faculty", "Previous", "Next",
    "Map and Directions", "APPLAUSE for Faculty. SUPPORT for Staff.",
}

# Canonical prefix for course codes that have multiple spellings on RMP.
_PREFIX_ALIASES: dict[str, str] = {
    "ITSC": "ITCS",
    "CSCI": "CSC",
}

_NAME_RE       = re.compile(r"^[A-Z][a-zA-Z'\-\.]+$")
_LAST_FIRST_RE = re.compile(r"^([A-Z][a-zA-Z'\-\.]+),\s+(.+)$")

_CREDENTIAL_PREFIX = re.compile(
    r"^(?:Dr\.?\s+|Prof\.?\s+|Mr\.?\s+|Ms\.?\s+|Mrs\.?\s+)+", re.IGNORECASE
)
_CREDENTIAL_SUFFIX = re.compile(
    r",\s*(?:Ph\.?D\.?|M\.?D\.?|M\.?S\.?|M\.?A\.?|MPH|MBA|PHD|JD|DDS|DO|RN|P\.?E\.?).*$",
    re.IGNORECASE,
)


def _flip_last_first(text: str) -> str:
    """Convert 'Last, First [Middle]' → 'First [Middle] Last'. No-op if not that format.

    Also normalizes ALL-CAPS input (e.g. 'ANDREONI, IGOR') to title case first.
    """
    # Normalize all-caps "LAST, FIRST" → "Last, First"
    if "," in text and text == text.upper():
        text = text.title()
    m = _LAST_FIRST_RE.match(text)
    if not m:
        return text
    last, rest = m.group(1), m.group(2)
    rest = re.sub(r",\s*(?:Jr\.?|Sr\.?|II|III|IV)$", "", rest).strip()
    return f"{rest} {last}"


def _clean_name(text: str) -> str:
    """Strip honorific prefixes and credential suffixes from a display name."""
    text = _CREDENTIAL_PREFIX.sub("", text).strip()
    text = _CREDENTIAL_SUFFIX.sub("", text).strip()
    return text


def _is_valid_faculty_name(text: str) -> bool:
    """Return True if text looks like a real person's name (2+ capitalized words, 5-40 chars)."""
    if not (5 <= len(text) <= 40):
        return False
    words = text.split()
    if len(words) < 2:
        return False
    # Every word must start with a capital letter; allow hyphenated/abbreviated names
    return all(_NAME_RE.match(w) for w in words)


def _dept_matches(rmp_dept: str, aliases: list[str]) -> bool:
    """Return True if rmp_dept contains any of the acceptable alias strings."""
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

        for a in soup.find_all("a", href=re.compile(r"/(directory|people)/[a-z][a-z\-0-9]+/$")):
            text = _clean_name(a.get_text(strip=True))
            if _is_valid_faculty_name(text) and text not in seen:
                seen.add(text)
                names.append(text)

        nxt = soup.find("a", string=re.compile(r"Next"))
        url  = nxt["href"] if nxt else None

    logger.info("Scraped %d unique faculty names", len(names))
    return names


def _scrape_playwright_names(
    directory_url: str,
    name_selector: str | None = None,
) -> list[str]:
    """Scrape faculty names using headless Chromium (for sites that block plain requests).

    By default finds links whose href matches faculty profile slug patterns and extracts
    the link text. If name_selector is provided, extracts the first line of each matching
    element instead (for pages where names aren't in link text, e.g. UNC Biology).
    """
    if not _PLAYWRIGHT_AVAILABLE:
        logger.error("playwright not installed — run: pip install playwright && python -m playwright install chromium")
        return []

    _PERSON_PATTERN = re.compile(
        r"/(person|people|faculty-member|faculty-profile|faculty)/[a-z][a-z\-0-9]+/?$"
    )

    seen:  set[str]  = set()
    names: list[str] = []

    logger.info("Fetching (playwright): %s", directory_url)
    try:
        with _sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page    = browser.new_page()
            page.goto(directory_url, wait_until="networkidle", timeout=20000)

            if name_selector:
                elements = page.query_selector_all(name_selector)
                for el in elements:
                    raw = el.inner_text().strip().split("\n")[0].strip()
                    text = _flip_last_first(_clean_name(raw))
                    if _is_valid_faculty_name(text) and text not in seen:
                        seen.add(text)
                        names.append(text)
            else:
                links = page.query_selector_all("a[href]")
                for link in links:
                    href = link.get_attribute("href") or ""
                    if not _PERSON_PATTERN.search(href):
                        continue
                    raw  = link.inner_text().strip()
                    if not raw:
                        continue
                    text = _flip_last_first(_clean_name(raw))
                    if _is_valid_faculty_name(text) and text not in seen:
                        seen.add(text)
                        names.append(text)

            browser.close()
    except Exception as e:
        logger.warning("Playwright scrape failed for %s: %s", directory_url, e)

    logger.info("Scraped %d unique faculty names (playwright)", len(names))
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
        slug        = source["school_slug"]
        rmp_name    = source["rmp_school_name"]
        dept        = source["department"]
        dir_url     = source["directory_url"]
        dept_aliases = source["rmp_dept_aliases"]

        logger.info("=== %s / %s ===", slug, dept)

        try:
            school_id = rmp_client.get_rmp_school_id(rmp_name)
            logger.info("RMP school ID: %s", school_id)
        except ValueError as e:
            logger.error("RMP school lookup failed for %r: %s", rmp_name, e)
            continue

        if source.get("scraper") == "playwright":
            names = _scrape_playwright_names(dir_url, name_selector=source.get("name_selector"))
        else:
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

            if not _dept_matches(prof["department"], dept_aliases):
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
