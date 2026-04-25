"""Fetches professor data and reviews from RateMyProfessor via GraphQL and page scraping."""
import base64
import json
import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
_TIMEOUT     = 5

_HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",
    "Content-Type":  "application/json",
    "User-Agent":    "ClassMate/0.1 (educational project)",
}

_school_id_cache: dict[str, str] = {}

_DEPT_MAP = {
    "ITCS": "Computer Science",
    "ITIS": "Computer Science",
    "ITSC": "Computer Science",
    "MATH": "Mathematics",
    "STAT": "Statistics",
    "CHEM": "Chemistry",
    "PHYS": "Physics",
    "PSYC": "Psychology",
    "ENGL": "English",
    "UWRT": "English",
    "BIOL": "Biology",
    "ECON": "Economics",
    "COMP": "Computer Science",
    "CSC":  "Computer Science",
    "MA":   "Mathematics",
    "ST":   "Statistics",
    "CH":   "Chemistry",
    "PY":   "Physics",
    "EC":   "Economics",
    "ENG":  "English",
}


def get_department_for_code(course_code: str) -> str:
    prefix = course_code.strip().split()[0].upper() if course_code.strip() else ""
    return _DEPT_MAP.get(prefix, prefix)


def _decode_id(encoded_id: str) -> str:
    """Decode base64 RMP node ID to numeric string. 'VGVhY2hlci0xMjM0' → '1234'."""
    try:
        padded  = encoded_id + "=" * (-len(encoded_id) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        return decoded.split("-")[-1]
    except Exception:
        return encoded_id


def get_rmp_school_id(school_name: str) -> str:
    """Return RMP school ID for school_name. Caches result. Raises ValueError if not found."""
    if school_name in _school_id_cache:
        return _school_id_cache[school_name]

    query = {
        "query": (
            f'{{ newSearch {{ schools(query: {{text: "{school_name}"}}) '
            f'{{ edges {{ node {{ id name }} }} }} }} }}'
        )
    }

    try:
        resp = requests.post(_GRAPHQL_URL, json=query, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise ValueError(f"RMP school lookup request failed: {e}") from e

    if not resp.ok:
        raise ValueError(f"RMP returned HTTP {resp.status_code} for school lookup")

    try:
        edges = resp.json()["data"]["newSearch"]["schools"]["edges"]
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"Unexpected RMP response shape during school lookup: {e}") from e

    if not edges:
        raise ValueError(f"No RMP school found for: {school_name!r}")

    school_id = edges[0]["node"]["id"]
    _school_id_cache[school_name] = school_id
    return school_id


def search_professor(school_id: str, professor_name: str) -> dict | None:
    """Search RMP for a professor at a school. Returns first match dict or None."""
    query = {
        "query": (
            f'{{ newSearch {{ teachers(query: {{text: "{professor_name}", schoolID: "{school_id}"}}) '
            f'{{ edges {{ node {{ id firstName lastName avgRating avgDifficulty '
            f'numRatings wouldTakeAgainPercent department }} }} }} }} }}'
        )
    }

    try:
        resp = requests.post(_GRAPHQL_URL, json=query, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        logger.warning("RMP professor search request failed (name=%r): %s", professor_name, e)
        return None

    if not resp.ok:
        logger.warning(
            "RMP returned HTTP %d for professor search (name=%r)",
            resp.status_code, professor_name,
        )
        return None

    try:
        edges = resp.json()["data"]["newSearch"]["teachers"]["edges"]
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Unexpected RMP response shape for professor search: %s", e)
        return None

    if not edges:
        return None

    node = edges[0]["node"]
    return {
        "id":               node.get("id", ""),
        "name":             f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
        "rating":           node.get("avgRating"),
        "difficulty":       node.get("avgDifficulty"),
        "num_ratings":      node.get("numRatings", 0),
        "department":       node.get("department", ""),
        "would_take_again": node.get("wouldTakeAgainPercent"),
    }


def get_department_professors(school_name: str, department_query: str, limit: int = 20) -> list[dict]:
    """Search RMP teachers by department query. Returns up to limit professors sorted by num_ratings desc."""
    try:
        school_id = get_rmp_school_id(school_name)
    except ValueError as e:
        logger.warning("Could not get school ID: %s", e)
        return []

    query = {
        "query": (
            f'{{ newSearch {{ teachers(query: {{text: "{department_query}", schoolID: "{school_id}"}}) '
            f'{{ edges {{ node {{ id firstName lastName avgRating avgDifficulty '
            f'numRatings wouldTakeAgainPercent department }} }} }} }} }}'
        )
    }

    try:
        resp = requests.post(_GRAPHQL_URL, json=query, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        logger.warning("RMP department search failed: %s", e)
        return []

    if not resp.ok:
        return []

    try:
        edges = resp.json()["data"]["newSearch"]["teachers"]["edges"]
    except (KeyError, ValueError, TypeError):
        return []

    professors = []
    for edge in edges[:limit]:
        node = edge.get("node", {})
        if node.get("id"):
            professors.append({
                "id":               node.get("id", ""),
                "name":             f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
                "rating":           node.get("avgRating"),
                "difficulty":       node.get("avgDifficulty"),
                "num_ratings":      node.get("numRatings", 0),
                "department":       node.get("department", ""),
                "would_take_again": node.get("wouldTakeAgainPercent"),
            })

    professors.sort(key=lambda p: p["num_ratings"] or 0, reverse=True)
    return professors


def get_professors_for_course(
    school_name: str,
    course_code: str,
    department_query: str,
    limit: int = 5,
) -> list[dict]:
    """Find professors who have been rated for course_code by fetching department professors
    and filtering their reviews to 2023+ reviews for this course."""
    candidates = get_department_professors(school_name, department_query, limit=20)

    normalized_code = course_code.replace(" ", "").upper()

    matched: list[dict] = []
    for prof in candidates:
        reviews = get_professor_reviews(prof["id"])
        recent_course_reviews = [
            r for r in reviews
            if r["class_name"].replace(" ", "").upper() == normalized_code
            and r.get("date", "")[:4] >= "2023"
        ]
        if recent_course_reviews:
            matched.append({**prof, "reviews": recent_course_reviews})

    matched.sort(key=lambda p: p["num_ratings"] or 0, reverse=True)
    return matched[:limit]


def get_professor_reviews(professor_id: str, limit: int = 10) -> list[dict]:
    """Fetch reviews by scraping the RMP professor page and parsing window.__RELAY_STORE__."""
    numeric_id = _decode_id(professor_id)
    url = f"https://www.ratemyprofessors.com/professor/{numeric_id}"

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _HEADERS["User-Agent"]},
            timeout=_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        logger.warning("RMP professor page request failed (id=%s): %s", numeric_id, e)
        return []

    if not resp.ok:
        logger.warning(
            "RMP returned HTTP %d for professor page (id=%s)", resp.status_code, numeric_id,
        )
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    relay_text: str | None = None
    for script in soup.find_all("script"):
        text = script.string or ""
        if "__RELAY_STORE__" in text:
            relay_text = text
            logger.debug("Found __RELAY_STORE__ script (id=%s, len=%d)", numeric_id, len(text))
            break

    if not relay_text:
        logger.debug("__RELAY_STORE__ not found on professor page (id=%s)", numeric_id)
        return []

    try:
        # Strip the "window.__RELAY_STORE__ = " prefix then raw_decode handles trailing JS
        json_str = relay_text.strip()
        json_str = json_str[json_str.index("{"):]
        store, _ = json.JSONDecoder().raw_decode(json_str)
        logger.debug("Parsed __RELAY_STORE__ with %d keys (id=%s)", len(store), numeric_id)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("Failed to parse __RELAY_STORE__ (id=%s): %s", numeric_id, e)
        return []

    ratings = [
        v for v in store.values()
        if isinstance(v, dict) and v.get("__typename") == "Rating"
    ]
    logger.debug("Found %d Rating records (id=%s)", len(ratings), numeric_id)

    reviews = []
    for node in ratings[:limit]:
        date_raw = node.get("date", "")
        reviews.append({
            "rating":      node.get("helpfulRating") or node.get("clarityRating"),
            "difficulty":  node.get("difficultyRating"),
            "review_text": node.get("comment", ""),
            "class_name":  node.get("class", ""),
            "date":        date_raw[:10],  # "2024-01-15 19:34:39 +0000 UTC" → "2024-01-15"
        })

    return reviews


if __name__ == "__main__":
    school_id = get_rmp_school_id("University of North Carolina at Charlotte")
    print(f"UNCC school ID: {school_id}")
    prof = search_professor(school_id, "Alex Chen")
    print(f"Professor search result: {prof}")
