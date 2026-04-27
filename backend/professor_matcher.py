"""Extracts professor names from Reddit posts and matches them against RateMyProfessor."""
import collections
import json
import logging
import pathlib
import re

from backend import rmp_client
from backend.rmp_client import get_department_for_code

logger = logging.getLogger(__name__)

_BASE = pathlib.Path(__file__).parent

try:
    with open(_BASE / "schools.json") as f:
        _SLUG_TO_RMP_NAME: dict[str, str] = {
            s["slug"]: s["rmp_school_name"]
            for s in json.load(f)
        }
except (FileNotFoundError, json.JSONDecodeError) as e:
    raise RuntimeError(f"Failed to load schools.json: {e}")

# Words that look like proper nouns but are not professor names
_STOP_WORDS: set[str] = {
    "Final", "Exam", "Homework", "Class", "Course", "Professor", "Instructor",
    "Lecture", "Syllabus", "Project", "Midterm", "Quiz", "Test", "Labs", "Lab",
    "ITCS", "MATH", "STAT", "CHEM", "PSYC", "UWRT", "COMP", "CSCI", "CSC",
    "CS", "IT", "AI", "ML", "DB",
    "Spring", "Fall", "Summer", "Winter", "Semester", "Canvas", "Blackboard",
    "Section", "Credit", "Hours", "School", "College", "University", "Department",
    "Office", "Next", "Last", "This", "First", "Second", "Third",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
    "Charlotte", "Chapel", "Hill", "Raleigh", "Carolina",
    "Good", "Great", "Easy", "Hard", "Intro", "Advanced", "General",
    # Subject / course-name words that look like names under _TWO_CAP
    "Discrete", "Linear", "Algebra", "Calculus", "Statistics", "Structures",
    "Analysis", "Theory", "Systems", "Networks", "Database", "Algorithms",
    "Biology", "Chemistry", "Physics", "Economics", "Psychology", "Engineering",
    "Science", "Math", "Computing", "Programming", "Differential", "Equations",
}

# Maps canonical dept name → acceptable RMP department substrings (case-insensitive contains)
_DEPT_ALIASES: dict[str, list[str]] = {
    "Computer Science": ["Computer Science", "Computing", "Computer Engineering",
                         "Information Technology", "Information Science", "Software Engineering"],
    "Mathematics":      ["Mathematics", "Math", "Applied Mathematics", "Statistics"],
    "Statistics":       ["Statistics", "Operations Research", "Biostatistics",
                         "Data Science", "Mathematics"],
    "Chemistry":        ["Chemistry", "Chemical", "Biochemistry"],
    "Physics":          ["Physics", "Astrophysics", "Engineering Physics"],
    "Psychology":       ["Psychology", "Psychological"],
    "English":          ["English", "Writing", "Communication", "Humanities"],
    "Biology":          ["Biology", "Biological"],
    "Economics":        ["Economics", "Finance"],
}


def _dept_matches_course(rmp_dept: str, expected_dept: str) -> bool:
    """Return True if rmp_dept is consistent with expected_dept."""
    aliases = _DEPT_ALIASES.get(expected_dept, [expected_dept])
    return any(alias.lower() in rmp_dept.lower() for alias in aliases)

# Explicit-title patterns — weighted higher in counting
_TITLED = re.compile(
    r'(?:Prof\.?|Professor|Dr\.?)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)',
    re.IGNORECASE,
)
# "with/took/had/take/taking CapName"
_CONTEXTUAL = re.compile(
    r'\b(?:with|took|had|taking|take)\s+([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})?)\b',
)
# "Last, First" format → normalised to "First Last"
_LAST_FIRST = re.compile(r'\b([A-Z][a-zA-Z]+),\s+([A-Z][a-zA-Z]+)\b')
# Two consecutive title-case words (broad heuristic, lowest weight)
_TWO_CAP = re.compile(r'\b([A-Z][a-z]{3,15})\s+([A-Z][a-z]{3,15})\b')


def _is_stop(name: str) -> bool:
    words = name.split()
    # Reject if any word is a known stop word
    if any(w in _STOP_WORDS for w in words):
        return True
    # Reject if any word is all-uppercase and 2–5 chars (dept/course codes: "MAT", "ITCS")
    if any(w == w.upper() and w.isalpha() and 2 <= len(w) <= 5 for w in words):
        return True
    return False


def extract_professor_names(posts: list[dict]) -> list[str]:
    """Scan post titles and bodies for professor name candidates. Returns top 5 by mentions."""
    counts: collections.Counter = collections.Counter()

    for post in posts:
        text = f"{post.get('title', '')} {post.get('body', '')}"

        for m in _TITLED.finditer(text):
            name = m.group(1).strip()
            if name[0].isupper() and not _is_stop(name):
                counts[name] += 2

        for m in _CONTEXTUAL.finditer(text):
            name = m.group(1).strip()
            if not _is_stop(name):
                counts[name] += 1

        for m in _LAST_FIRST.finditer(text):
            name = f"{m.group(2)} {m.group(1)}"
            if not _is_stop(name):
                counts[name] += 2

        for m in _TWO_CAP.finditer(text):
            first, last = m.group(1), m.group(2)
            if first not in _STOP_WORDS and last not in _STOP_WORDS:
                counts[f"{first} {last}"] += 1

    return [name for name, _ in counts.most_common(5)]


def get_professor_data(school_slug: str, professor_name: str) -> dict | None:
    """Look up a professor on RMP and return their data + reviews, or None if not found."""
    rmp_school_name = _SLUG_TO_RMP_NAME.get(school_slug)
    if not rmp_school_name:
        logger.warning("Unknown school slug: %r", school_slug)
        return None

    try:
        school_id = rmp_client.get_rmp_school_id(rmp_school_name)
    except ValueError as e:
        logger.warning("Could not get RMP school ID for %r: %s", rmp_school_name, e)
        return None

    professor = rmp_client.search_professor(school_id, professor_name)
    if not professor:
        return None

    reviews = rmp_client.get_professor_reviews(professor["id"])
    return {**professor, "reviews": reviews}


def match_professors(school_slug: str, posts: list[dict], course_code: str) -> list[dict]:
    """Extract professor names from posts, match on RMP, return sorted by num_ratings."""
    candidates = extract_professor_names(posts)
    logger.info(
        "Extracted %d candidate names from %d posts: %s",
        len(candidates), len(posts), candidates,
    )

    expected_dept = get_department_for_code(course_code)
    results: list[dict] = []
    seen_ids: set[str] = set()

    for name in candidates:
        prof = get_professor_data(school_slug, name)
        if not prof or prof["id"] in seen_ids:
            continue
        rmp_dept = prof.get("department", "")
        if expected_dept and not _dept_matches_course(rmp_dept, expected_dept):
            logger.warning(
                "Dept mismatch for %r: RMP says %r, expected %r — skipping",
                name, rmp_dept, expected_dept,
            )
            continue
        seen_ids.add(prof["id"])
        results.append(prof)

    results.sort(key=lambda p: p.get("num_ratings") or 0, reverse=True)
    return results


if __name__ == "__main__":
    from backend import reddit_client
    posts = reddit_client.get_professor_posts("UNCCharlotte", "ITCS 1213", "ITCS 1213", limit=10)
    print(f"Got {len(posts)} posts from Reddit")
    professors = match_professors("uncc", posts, "ITCS 1213")
    print(f"Matched {len(professors)} professors on RMP:")
    for p in professors:
        print(f"  {p['name']} — {p['num_ratings']} ratings, {p['rating']}/5")
