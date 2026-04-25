"""Fetches professor discussions from school subreddits via Reddit's public JSON endpoint."""
import logging

import requests

logger = logging.getLogger(__name__)

_USER_AGENT  = "ClassMate/0.1 (educational project)"
_TIMEOUT     = 5  # seconds
_SEARCH_URL  = "https://www.reddit.com/r/{subreddit}/search.json"


def fetch_reddit_posts(subreddit: str, query: str, limit: int = 20) -> list[dict]:
    url     = _SEARCH_URL.format(subreddit=subreddit)
    params  = {"q": query, "restrict_sr": 1, "sort": "relevance", "limit": limit, "t": "year"}
    headers = {"User-Agent": _USER_AGENT}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    except requests.exceptions.Timeout:
        logger.warning("Reddit request timed out (subreddit=%s, query=%r)", subreddit, query)
        return []
    except requests.exceptions.RequestException as e:
        logger.warning("Reddit request failed (subreddit=%s, query=%r): %s", subreddit, query, e)
        return []

    if not response.ok:
        logger.warning(
            "Reddit returned HTTP %d (subreddit=%s, query=%r)",
            response.status_code, subreddit, query,
        )
        return []

    try:
        children = response.json()["data"]["children"]
    except (KeyError, ValueError) as e:
        logger.warning("Unexpected Reddit response shape: %s", e)
        return []

    posts = []
    for child in children:
        post = child.get("data", {})
        posts.append({
            "id":       post.get("id", ""),
            "title":    post.get("title", ""),
            "body":     post.get("selftext", ""),
            "score":    post.get("score", 0),
            "url":      post.get("url", ""),
            "comments": [],
        })

    return posts


def get_professor_posts(
    subreddit: str,
    professor_name: str,
    course_code: str,
    limit: int = 15,
) -> list[dict]:
    by_name = fetch_reddit_posts(subreddit, professor_name, limit=limit)
    by_code = fetch_reddit_posts(subreddit, course_code,    limit=limit)

    seen:     set[str]   = set()
    combined: list[dict] = []

    for post in by_name + by_code:
        if post["id"] not in seen:
            seen.add(post["id"])
            combined.append(post)

    combined.sort(key=lambda p: p["score"], reverse=True)
    return combined[:limit]


if __name__ == "__main__":
    posts = get_professor_posts("UNCCharlotte", "Alex Chen", "ITCS 1213", limit=5)
    print(f"Found {len(posts)} posts")
    for p in posts[:3]:
        print(f"  [{p['score']}] {p['title'][:80]}")
