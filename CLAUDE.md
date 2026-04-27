# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ClassMate: AI-powered course insight tool for US college students. User inputs a school + course; ClassMate returns a ranked professor list with AI-synthesized insights from Reddit and RateMyProfessor.

**Status:** v1 in active development. Targets UNCC, UNC Chapel Hill, NC State. Most `backend/` source files are stubs — implementation is in progress.

## Running locally

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Requires a `.env` file (gitignored) with:
- `ANTHROPIC_API_KEY`
- Reddit OAuth credentials (for PRAW)

## Validating school config

```bash
python backend/validate_schools.py
```

Verifies each school's subreddit exists and is active via the Reddit API.

## Architecture

**Request flow:**

```
POST /insights  →  course_resolver  →  reddit_client + rmp_client
                                    ↓
                              professor_matcher
                                    ↓
                        aggregator + sentiment (VADER)
                                    ↓
                            summarizer (Claude API)
                                    ↓
                            insights  →  JSON response
```

**Key source files** (all in `backend/`):

| File | Role |
|------|------|
| `main.py` | FastAPI app, route definitions |
| `course_resolver.py` | Maps user course input to catalog entries |
| `reddit_client.py` | Fetches professor discussions from school subreddits via PRAW |
| `rmp_client.py` | Fetches ratings/reviews from RateMyProfessor |
| `professor_matcher.py` | Aligns professor names across Reddit + RMP sources |
| `aggregator.py` | Combines raw data from all sources |
| `sentiment.py` | VADER-based tone scoring |
| `summarizer.py` | Calls Claude API to synthesize final professor insights |
| `insights.py` | Structures and ranks the final output |

**Config files:**

| File | Purpose |
|------|---------|
| `backend/schools.json` | School slugs, subreddit names, RMP school names, brand colors |
| `backend/courses.json` | Course catalog (currently empty stub) |

School config shape: `{ slug, display_name, subreddit, rmp_school_name, primary_color }`. When adding a new school, run `validate_schools.py` to confirm the subreddit is active.

## Claude API usage

The `summarizer.py` module calls the Anthropic Claude API. Use prompt caching where possible for repeated system prompts. Default to `claude-sonnet-4-6` unless reasoning quality requires Opus.

## Faculty cache known limitations

NC State sciences departments (Mathematics, Statistics, Physics, Chemistry, Biology) have no
scrapeable faculty directory. The `*.sciences.ncsu.edu/people/` subdomains return a college-wide
listing unfiltered by department; `/people/faculty/` returns 404 on all of them. CHASS departments
(Economics, English, Psychology) either DNS-fail or time out from this IP. Only CS
(`csc.ncsu.edu/faculty/`), ECE (`ece.ncsu.edu/people/`), and MAE (`mae.ncsu.edu/people/`) are
confirmed working for NC State. Revisit when NC State reorganises its web infrastructure.

## Spec

Full v1.0 technical specification: `docs/classMate.pdf`
