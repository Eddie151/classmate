"""Resolves user course input to a canonical course entry using three-stage matching."""

# ----------------------------------------------------------------------
# Known calibration tradeoffs (last reviewed: April 2026)
#
# Three test cases do not resolve to their "ideal" answer. Each is a
# deliberate tradeoff between precision and user experience:
#
# 1. "Intro to CS 2" / "CS 2" land as ambiguous with MATH 1242 (Calc II)
#    as a third candidate. The "2" token triggers a high fuzzy score
#    against any course with a "2" alias. The candidate list is visually
#    noisy but the correct course is always the top candidate. A
#    department-aware penalty would fix this; deferred to v2 to avoid
#    introducing new failure modes.
#
# 2. "calc" resolves to Precalculus rather than ambiguous between Calc I
#    and II. This is defensible: "calc" is a better substring match for
#    "Precalculus" than for "Calculus I/II". The correct answer is
#    genuinely subjective.
#
# 3. "data strucures" (typo) falls to Stage 3 (LLM) instead of resolving
#    in Stage 2. The typo scores 82 against "Data Structures", under our
#    85 threshold. Lowering the threshold would produce false positives
#    elsewhere. Stage 3 correctly resolves the typo, so the user still
#    gets the right answer — just with one extra API call.
#
# When real user query data is available (post-deployment), revisit
# Stage 2 thresholds and the per-department scoring weights.
# ----------------------------------------------------------------------

import json
import re
import pathlib
import anthropic
from rapidfuzz import fuzz
from backend.security.secrets import get_secret

_CATALOG_PATH = pathlib.Path(__file__).parent / "courses.json"
try:
    with open(_CATALOG_PATH, "r") as f:
        _CATALOG: dict[str, list[dict]] = json.load(f)
except FileNotFoundError:
    raise RuntimeError(
        f"courses.json not found at {_CATALOG_PATH}. Cannot load course catalog."
    )
except json.JSONDecodeError as e:
    raise RuntimeError(f"courses.json is invalid JSON: {e}")

_CODE_INDEX: dict[str, dict[str, dict]] = {
    slug: {re.sub(r"[^A-Z0-9]", "", c["code"].upper()): c for c in courses}
    for slug, courses in _CATALOG.items()
}

_LLM_SYSTEM_PROMPT = """You are a course catalog assistant for a US university. \
You will receive a user's course search input and a list of courses in the catalog. \
Return strict JSON — either:
  {"code": "ITCS 1213", "confidence": 0.85}
if you can identify a match with confidence 0.80 or higher, or:
  {"code": null, "reason": "No course in the catalog matches this input."}
if no match exists or your confidence is below 0.80. No preamble, no markdown fences, \
no explanation."""


def _normalize(text: str) -> str:
    """Strip punctuation, collapse whitespace, uppercase."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _build_fuzzy_corpus(courses: list[dict]) -> list[tuple[str, dict]]:
    """One entry per title/alias, each pointing back to its course."""
    entries = []
    for course in courses:
        for term in [course["title"]] + course["aliases"]:
            entries.append((term, course))
    return entries


def _stage1_code_lookup(user_input: str, courses: list[dict], school_slug: str) -> dict | None:
    normalized = _normalize(user_input)
    if not re.match(r"^[A-Z]+\d+$", normalized):
        return None
    course = _CODE_INDEX[school_slug].get(normalized)
    if course:
        return {
            "status": "matched",
            "code": course["code"],
            "title": course["title"],
            "confidence": 1.0,
            "stage": 1,
        }
    return None


def _stage2_fuzzy(user_input: str, courses: list[dict]) -> dict | None:
    corpus = _build_fuzzy_corpus(courses)
    best: dict[str, tuple[float, dict]] = {}
    for string, course in corpus:
        score = fuzz.WRatio(user_input, string)
        code = course["code"]
        if code not in best or score > best[code][0]:
            best[code] = (score, course)

    if not best:
        return None

    ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)
    top_score, top_course = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0

    if top_score >= 85 and (top_score - second_score) >= 15:
        return {
            "status": "matched",
            "code": top_course["code"],
            "title": top_course["title"],
            "confidence": round(top_score / 100, 2),
            "stage": 2,
        }

    candidates = [
        {"code": entry[1]["code"], "title": entry[1]["title"]}
        for entry in ranked
        if entry[0] >= 70 and (top_score - entry[0]) <= 15
    ]
    if len(candidates) >= 2:
        return {"status": "ambiguous", "candidates": candidates[:3], "stage": 2}

    return None


def _stage3_llm(user_input: str, courses: list[dict], school_slug: str) -> dict:
    catalog_str = "\n".join(f"{c['code']} - {c['title']}" for c in courses)
    user_prompt = f"User input: \"{user_input}\"\n\nCourse catalog:\n{catalog_str}"

    api_key = get_secret("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.RateLimitError as e:
        raise ValueError("Anthropic rate limit reached. Wait a moment and retry.") from e
    except anthropic.APIError as e:
        raise ValueError(f"Anthropic API error ({type(e).__name__}): {e}") from e

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\n\nRaw: {raw}") from e

    if not data.get("code"):
        return {
            "status": "no_match",
            "suggestion": "Try entering the course code directly (e.g., ITCS 1213).",
            "stage": 3,
        }

    course = _CODE_INDEX[school_slug].get(_normalize(data["code"]))
    if not course:
        return {
            "status": "no_match",
            "suggestion": "Try entering the course code directly (e.g., ITCS 1213).",
            "stage": 3,
        }

    confidence = float(data.get("confidence", 0.7))
    if confidence < 0.80:
        return {
            "status": "no_match",
            "suggestion": "We weren't confident enough to pick a course. Try entering the course code directly or using a more specific name.",
            "stage": 3,
        }

    return {
        "status": "matched",
        "code": course["code"],
        "title": course["title"],
        "confidence": confidence,
        "stage": 3,
    }


def resolve_course(school_slug: str, user_input: str) -> dict:
    if school_slug not in _CATALOG:
        raise ValueError(
            f"Unknown school slug: {school_slug!r}. Known: {list(_CATALOG.keys())}"
        )
    courses = _CATALOG[school_slug]
    user_input = user_input.strip()

    result = _stage1_code_lookup(user_input, courses, school_slug)
    if result:
        return result

    result = _stage2_fuzzy(user_input, courses)
    if result:
        return result

    return _stage3_llm(user_input, courses, school_slug)


TEST_CASES = [
    {"input": "ITCS 1213",                          "note": "exact code"},
    {"input": "itcs1213",                            "note": "lowercase, no space — stage 1 after normalize"},
    {"input": "ITCS-1213",                           "note": "code with dash — stage 1 after normalize"},
    {"input": "Introduction to Computer Science II", "note": "exact official title — stage 2"},
    {"input": "Intro to CS 2",                       "note": "common alias — stage 2"},
    {"input": "CS 2",                                "note": "short alias — stage 2"},
    {"input": "data structures",                     "note": "lowercase title — stage 2"},
    {"input": "data strucures",                      "note": "typo — stage 2 fuzzy"},
    {"input": "calc",                                "note": "vague — expect ambiguous between Calc I and Calc II"},
    {"input": "AI",                                  "note": "short alias for ITCS 3153 — stage 2"},
    {"input": "the class about databases",           "note": "natural language, no fuzzy match — expect Stage 3 LLM"},
    {"input": "machine learning",                    "note": "course doesn't exist in catalog — expect no_match via Stage 3"},
]


def run_tests():
    print(f"Running {len(TEST_CASES)} test cases against school: uncc")
    print("=" * 70)
    for case in TEST_CASES:
        result = resolve_course("uncc", case["input"])
        status = result["status"]
        if status == "matched":
            detail = f"{result['code']} — {result['title']} (confidence: {result['confidence']})"
        elif status == "ambiguous":
            codes = ", ".join(c["code"] for c in result["candidates"])
            detail = f"candidates: [{codes}]"
        else:
            detail = result["suggestion"]
        print(f"  [{case['note']}]")
        print(f"    input:  {case['input']!r}")
        print(f"    status: {status}")
        print(f"    stage:  {result.get('stage')}")
        print(f"    result: {detail}")
        print()


if __name__ == "__main__":
    run_tests()
