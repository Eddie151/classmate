"""
Generates backend/supported_courses.json from faculty_cache.json.

Run this whenever faculty_cache.json is refreshed:
    python -m backend.build_supported_courses

Output: backend/supported_courses.json
  {
    "<school_slug>": [
      {
        "course_code":     "ITCS 3153",
        "course_name":     "Introduction to Artificial Intelligence",
        "professor_count": 1,
        "review_count":    5,
        "last_updated":    "2026-05-11"
      },
      ...
    ],
    ...
  }

professor_count: number of professors in the cache who list this course in courses_taught
review_count:    total RMP reviews stored for those professors (proxy for data richness)
"""

import json
import pathlib
from datetime import date
from collections import defaultdict

BASE = pathlib.Path(__file__).parent

# ── load inputs ───────────────────────────────────────────────────────────────

with open(BASE / "faculty_cache.json") as f:
    FACULTY_CACHE: dict = json.load(f)

with open(BASE / "courses.json") as f:
    CATALOG: dict[str, list[dict]] = json.load(f)

TODAY = date.today().isoformat()

# ── build title lookup per school: course_code -> title ──────────────────────

def _build_title_index(catalog: dict[str, list[dict]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for slug, courses in catalog.items():
        index[slug] = {c["code"]: c["title"] for c in courses}
    return index

TITLE_INDEX = _build_title_index(CATALOG)

# ── collect supported courses per school ─────────────────────────────────────

PREFIX_ALIASES = {"ITSC": "ITCS", "ITCS": "ITSC"}

def _normalize_code(code: str) -> str:
    """'ITCS1212' or 'ITCS 1212' -> 'ITCS 1212' canonical form."""
    import re
    m = re.match(r"^([A-Z]+)\s*(\d+\w*)$", code.strip().upper())
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return code.strip().upper()


def build_supported(slug: str) -> list[dict]:
    depts = FACULTY_CACHE.get(slug, {})
    title_map = TITLE_INDEX.get(slug, {})

    # course_code -> {prof_count, review_count}
    course_stats: dict[str, dict] = defaultdict(lambda: {"professor_count": 0, "review_count": 0})

    for dept_name, profs in depts.items():
        for prof in profs:
            for raw_code in prof.get("courses_taught", []):
                code = _normalize_code(raw_code)
                course_stats[code]["professor_count"] += 1
                course_stats[code]["review_count"] += len(prof.get("reviews", []))

    results = []
    for code, stats in sorted(course_stats.items()):
        title = title_map.get(code, "")

        # If the exact code isn't in the catalog, try the alias prefix
        if not title:
            prefix = code.split()[0]
            alias = PREFIX_ALIASES.get(prefix)
            if alias:
                alias_code = alias + code[len(prefix):]
                title = title_map.get(alias_code, "")

        # Skip courses with no title (not in catalog — stale cache entry)
        if not title:
            continue

        results.append({
            "course_code":     code,
            "course_name":     title,
            "professor_count": stats["professor_count"],
            "review_count":    stats["review_count"],
            "last_updated":    TODAY,
        })

    return results


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    output: dict[str, list[dict]] = {}
    total = 0

    for slug in ["uncc", "unc", "ncsu"]:
        courses = build_supported(slug)
        output[slug] = courses
        total += len(courses)
        print(f"{slug}: {len(courses)} supported courses")

    out_path = BASE / "supported_courses.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWrote {total} total courses to {out_path}")
