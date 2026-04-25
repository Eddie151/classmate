"""Generates Tier 1 structured professor insights using Claude Haiku."""
import json
import anthropic
from backend.security.secrets import get_secret

SYSTEM_PROMPT = """You are analyzing student-generated discussion about a specific professor \
teaching a specific course at a US university. Your task is to produce a structured JSON \
analysis of that professor for that course.

You will be given:
- A list of Reddit posts (each with a title, body, and upvotes)
- A list of RateMyProfessor reviews (each with a rating, difficulty, and comment)

Produce a JSON object with exactly these 9 fields:

1. "difficulty_profile" (string or null): One sentence describing WHAT KIND of hard the course \
is, not just how hard. Example: "Hard because of fast pace, not content depth". Return null if \
evidence is too thin to support a confident claim.

2. "workload_shape" ("front_loaded", "back_loaded", "steady", or null): How workload is \
distributed across the semester. Return null if posts do not describe pacing.

3. "hidden_prerequisites" (string or null): One sentence on de facto prerequisites students \
mention beyond the catalog. Example: "Comfort with recursion is mentioned by multiple students". \
Return null if no clear pattern emerges.

4. "take_if" (string or null): One sentence starting with a positive condition describing who \
should take this course. Example: "You learn well from hands-on projects". Return null if no \
clear pattern.

5. "skip_if" (string or null): One sentence starting with a cautionary condition describing who \
should avoid this course. Example: "You need lots of feedback on assignments". Return null if no \
clear pattern.

6. "effort_to_grade" ("generous_curve", "standard", "weeder", or "unknown"): How effort maps to \
grades.
- "generous_curve" requires MULTIPLE students describing the course as easy, an easy A, lots of \
extra credit, or a notably lenient curve. A single mention of "curve helped" is NOT enough.
- "weeder" requires multiple students describing the course as designed to fail students, 60%+ \
of students failing, curve that hurts rather than helps, or explicitly calling it a "weeder."
- "standard" is the default for normal courses where effort roughly matches grade.
- "unknown" when students do not discuss the grading dynamics at all.
NEVER return null.

7. "summary" (string, never null): 3 to 5 sentences synthesizing all available data about this \
professor and course. If data is extremely thin, the summary must explicitly say so.

8. "confidence" ("high", "medium", or "low"): Confidence in the analysis, reflecting actual \
evidence strength. Low sample size OR weak consensus must result in "low". NEVER return null.

9. "sample_size" (integer, never null): The total number of Reddit posts plus RateMyProfessor \
reviews provided as input.

Rules:
- DO NOT invent, guess, or extrapolate. If evidence is weak or absent, return null for nullable \
fields.
- Output ONLY a valid JSON object. No preamble, no markdown fences, no explanation.
- Every one of the 9 fields must be present in the output.
- Reviews and posts have dates. Prioritize patterns from 2023 onwards. If older evidence \
contradicts recent evidence, weight recent evidence more heavily. If all evidence is old \
(pre-2023), note this explicitly in the summary.
"""

_WORKLOAD_SHAPE_VALUES = {"front_loaded", "back_loaded", "steady", None}
_EFFORT_TO_GRADE_VALUES = {"generous_curve", "standard", "weeder", "unknown"}
_CONFIDENCE_VALUES = {"high", "medium", "low"}


def _extract_json(raw: str) -> str:
    """Strip markdown code fences if present, returning bare JSON text."""
    text = raw.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _validate(data: dict, expected_sample_size: int) -> None:
    required_keys = {
        "difficulty_profile", "workload_shape", "hidden_prerequisites",
        "take_if", "skip_if", "effort_to_grade", "summary",
        "confidence", "sample_size",
    }
    missing = required_keys - data.keys()
    if missing:
        raise ValueError(f"Response missing required keys: {missing}")

    if data["workload_shape"] not in _WORKLOAD_SHAPE_VALUES:
        raise ValueError(
            f"Invalid workload_shape: {data['workload_shape']!r}. "
            f"Must be one of {_WORKLOAD_SHAPE_VALUES}."
        )

    if data["effort_to_grade"] not in _EFFORT_TO_GRADE_VALUES:
        raise ValueError(
            f"Invalid effort_to_grade: {data['effort_to_grade']!r}. "
            f"Must be one of {_EFFORT_TO_GRADE_VALUES}."
        )

    if data["confidence"] not in _CONFIDENCE_VALUES:
        raise ValueError(
            f"Invalid confidence: {data['confidence']!r}. "
            f"Must be one of {_CONFIDENCE_VALUES}."
        )

    if not isinstance(data["sample_size"], int):
        raise ValueError(
            f"sample_size must be an integer, got {type(data['sample_size']).__name__}."
        )
    if data["sample_size"] != expected_sample_size:
        raise ValueError(
            f"sample_size mismatch: expected {expected_sample_size}, "
            f"got {data['sample_size']}."
        )

    if not isinstance(data["summary"], str) or not data["summary"].strip():
        raise ValueError("summary must be a non-empty string.")

    for field in ("difficulty_profile", "hidden_prerequisites", "take_if", "skip_if"):
        value = data[field]
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"{field} must be a string or null, got {type(value).__name__}."
            )


def generate_insights(
    professor_name: str,
    course_code: str,
    reddit_posts: list[dict],
    rmp_reviews: list[dict],
) -> dict:
    api_key = get_secret("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    user_prompt = (
        f"Professor: {professor_name}\n"
        f"Course: {course_code}\n\n"
        f"Reddit posts ({len(reddit_posts)} total):\n"
        f"{json.dumps(reddit_posts, indent=2)}\n\n"
        f"RateMyProfessor reviews ({len(rmp_reviews)} total):\n"
        f"{json.dumps(rmp_reviews, indent=2)}"
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.RateLimitError as e:
        raise ValueError(
            "Anthropic rate limit reached. Wait a moment and retry."
        ) from e
    except anthropic.APIError as e:
        raise ValueError(
            f"Anthropic API error ({type(e).__name__}): {e}"
        ) from e

    raw = message.content[0].text
    cleaned = _extract_json(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returned invalid JSON: {e}\n\nRaw response:\n{raw}"
        ) from e

    _validate(data, expected_sample_size=len(reddit_posts) + len(rmp_reviews))

    return data


MOCK_DATA = {
    "professor_name": "Dr. Alex Chen",
    "course_code": "ITCS 1213",
    "reddit_posts": [
        {
            "id": "abc1",
            "title": "Chen for 1213 next sem?",
            "body": "took him last spring. great teacher, projects are brutal in the last 3 weeks but you actually learn. office hours are worth it. b+ from me.",
            "score": 24,
            "comments": [
                "agreed, the recursion project almost broke me",
                "go to every lecture, his notes on canvas are incomplete"
            ]
        },
        {
            "id": "abc2",
            "title": "1213 midterm tips",
            "body": "mid class. exams are tricky but fair. study the practice problems he gives out, thats basically the test.",
            "score": 8,
            "comments": [
                "chen or wilkes, anyone compare?"
            ]
        },
        {
            "id": "abc3",
            "title": "AVOID 1213 WITH CHEN if you hate group work",
            "body": "40% of your grade is a group project. if you get stuck with bad partners you are cooked. i got a C because 2 ppl ghosted.",
            "score": 17,
            "comments": [
                "this. happened to me too",
                "you can request a partner swap but you have to email him early",
                "just do the work yourself honestly"
            ]
        }
    ],
    "rmp_reviews": [
        {
            "rating": 4,
            "difficulty": 4,
            "review_text": "Demanding but fair. You will work harder than in other sections but his feedback on code is detailed. Come prepared to every class.",
            "would_take_again": True,
            "date": "2025-05-14"
        },
        {
            "rating": 3,
            "difficulty": 5,
            "review_text": "Projects stacked up at the end of the semester. Group work was rough. Curve helped. Would not take again unless I had to.",
            "would_take_again": False,
            "date": "2024-12-03"
        }
    ]
}


def run_mock_test():
    """Run insights generation on MOCK_DATA and print the structured output."""
    result = generate_insights(
        professor_name=MOCK_DATA["professor_name"],
        course_code=MOCK_DATA["course_code"],
        reddit_posts=MOCK_DATA["reddit_posts"],
        rmp_reviews=MOCK_DATA["rmp_reviews"],
    )
    print("Structured insight output for", MOCK_DATA["professor_name"], "teaching", MOCK_DATA["course_code"])
    print("=" * 70)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    run_mock_test()
