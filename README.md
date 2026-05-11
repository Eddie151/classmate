# ClassMate

Pick the right professor in 15 seconds. Enter a course at UNCC, UNC Chapel Hill, or NC State — ClassMate returns a ranked professor list with AI-synthesized insights drawn from Reddit and RateMyProfessor.

**[Live →](https://classmate-m8aj.onrender.com)**

Currently covers 440 courses across all three schools. Adding more weekly.

---

## Screenshots

_Coming soon._

---

## Tech stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.13, FastAPI |
| AI synthesis | Claude Haiku (Anthropic API) |
| Data sources | RateMyProfessor (GraphQL), Reddit (PRAW) |
| Sentiment | VADER |
| Frontend | Vanilla HTML / CSS / JS |
| Hosting | Render |

---

## Local development

**Prerequisites:** Python 3.13+

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Open `index.html` directly in a browser, or visit `http://localhost:8000`.

### Environment variables

Create a `.env` file at the project root (never committed):

```
ANTHROPIC_API_KEY=...

REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=ClassMate/0.1 by u/<your_reddit_username>
```

To get Reddit credentials: [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) → **create another app** → select **script** → copy the client ID and secret.

### Validate school config

```bash
python backend/validate_schools.py
```

Confirms each school's subreddit is active via the Reddit API.

---

## License

MIT — see [LICENSE](LICENSE).

---

_Built by [Aadhyant Bhatnagar](https://github.com/aadhyantbhatnagar), CS @ UNC Charlotte._
