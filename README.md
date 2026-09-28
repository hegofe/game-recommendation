# Casino Game Recommender

Recommendation API for an online casino. When a player logs in, it returns 5 personalised games based on their play history and global popularity, each with a short reason written by an LLM. The LLM never blocks the login: it runs in the background and never decides what is recommended.

## Overview

- The web app calls a single endpoint at login. Recommendations are computed from the player's last 100 plays (DynamoDB) and a global popularity counter (Redis), then cached for 24 hours, so repeat logins take a few milliseconds.
- Scoring blends the player's tastes (category and provider shares) with popularity. The weight of the tastes grows with the number of plays (`min(plays / 10, 1)`), so a new player gets the most popular games, one per category, and recommendations become personal as they play.
- Business rules run after scoring: games already played are excluded and at most 3 games per provider are returned.
- Every game comes with a reason. A template reason is returned immediately, and an LLM (Gemini or a local Ollama model, through LangChain) rewrites all 5 in a single background call. The next login shows the AI reasons, and `reason_source` tells which one the client got.
- Recording a play updates DynamoDB, increments the game's popularity and invalidates the player's cached recommendations, so the next login reflects it.

## Architecture

### Read path — login recommendations

```text
          Casino web app
                |
                | GET /users/{id}/recommendations
                v
             FastAPI
                |
                | cache hit? ---------- yes ---> 200 (a few ms)
                v no
      +---------+----------+
      |                    |
      v                    v
   DynamoDB              Redis
  last 100 plays       popularity
      \                    /
       v                  v
    profile + scoring + business rules
    (S3 catalog, loaded in memory)
                |
                | top 5 + template reasons
                v
        Redis reco:{id} (24 h) ---> 200 (~100 ms)
                |
                | background task
                v
     LangChain -> Gemini / Ollama
  one call, 5 reasons, JSON schema
                |          \
                |           \ error / timeout / rejected by filter
                |            v
                |      keep template reasons
                v
     quality filter -> replace reasons in Redis
     (only if the cached entry is still the same)
```

### Write path — record a play

```text
          Casino web app
                |
                | POST /users/{id}/plays
                v
             FastAPI
       /        |          \
      v         v           v
  DynamoDB    Redis        Redis
  put play  ZINCRBY       DEL reco:{id}
            popularity    (next login recomputes)
```

Two things worth calling out: the **LLM is off the request path** (the login latency doesn't depend on a model that takes 15-45 s on a CPU or on a busy free tier), and it **only writes text**. What gets recommended is decided by plain Python scoring, so the model can't push a game or invent a reason for one it wasn't given.

## Tech stack

| Layer | Technology |
|---|---|
| Language / runtime | Python 3.12 |
| HTTP | FastAPI 0.141, Uvicorn, Pydantic 2 |
| Storage | AWS S3 (game catalog), DynamoDB (play history) via boto3, emulated locally with [MiniStack](https://github.com/ministackorg/ministack) |
| Cache | Redis 7 (recommendations cache, popularity sorted set) |
| LLM | LangChain 1.x (`with_structured_output`) over Gemini (`gemini-3.5-flash-lite`) or Ollama (`qwen2.5:1.5b`) |
| Infrastructure | Docker Compose |

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/users/{user_id}/recommendations` | 5 recommended games with reasons; called at login |
| `POST` | `/users/{user_id}/plays` | Record a play |
| `GET` | `/users/{user_id}/plays` | Play history, newest first |

Interactive docs (Swagger) are served at `/docs`.

### Get recommendations

```
GET /users/7/recommendations
```

Returns:

```json
{
  "user_id": 7,
  "generated_at": "2026-09-28T17:18:29.575284Z",
  "recommendations": [
    {
      "game_id": 6,
      "name": "Rise of Wolves",
      "category": "slots",
      "provider": "Play'n GO",
      "score": 0.8364,
      "reason": "You will love this Play'n GO slot game since you play them often.",
      "reason_source": "ai"
    }
  ]
}
```

- `reason_source` is `template` on the first login after a (re)computation, and `ai` once the background LLM call has finished and its reasons passed the quality filter.
- `user_id` must be a positive integer; anything else returns `422`. A user with no plays is valid and gets the most popular games.

### Record a play

```json
POST /users/7/plays
{
  "game_id": 12
}
```

- Returns `201 Created` with the game and the play timestamp.
- An unknown `game_id` returns `404`.
- The player's cached recommendations are invalidated, so the next login recomputes them.

### Get play history

```
GET /users/7/plays?limit=100
```

Returns the plays newest first, with each game's name, category and provider. `limit` defaults to 100 (max 1000).

## Running locally

### Prerequisites

- Docker Desktop (or another Docker engine) with Docker Compose
- Optional: a free [Gemini API key](https://aistudio.google.com/apikey). Without one, the API works and returns template reasons only.
- Optional: about 1 GB of free disk to run the LLM locally with Ollama instead

### Quick start

Create a `.env` file in the project root with your key. The file is git-ignored; `.env.example` lists every setting.

```
GOOGLE_API_KEY=your-key
```

**Windows (PowerShell):**

```powershell
# 1. Start the API, Redis and MiniStack (S3 + DynamoDB)
docker compose up -d --build

# 2. Load synthetic data: 200 games, 50 players, ~4,400 plays
docker compose exec api python -m scripts.seed
```

**Linux / macOS (bash):**

```bash
# 1. Start the API, Redis and MiniStack (S3 + DynamoDB)
docker compose up -d --build

# 2. Load synthetic data: 200 games, 50 players, ~4,400 plays
docker compose exec api python -m scripts.seed
```

MiniStack keeps its data in memory, so run the seed again after restarting it. The seed is deterministic and can be re-run at any time to reset the data.

To use a local LLM instead of Gemini, set `LLM_PROVIDER=ollama` in `.env` and start the `ollama` profile. The model is pulled into a named volume on first start.

```
docker compose --profile ollama up -d
```

### Run the demo

The seed creates players with different amounts of history:

| user_id | Plays | Good for |
|---|---|---|
| 1-5 | 1-2 | Very little history |
| 6-10 | 3-9 | Some history |
| 11-50 | 10-200 | Clear preferences (e.g. `30`: crash and Play'n GO slots; `42`: live casino) |
| 51+ | 0 | New player |

Game ids: 1-110 slots, 111-130 roulette, 131-150 blackjack, 151-180 live casino, 181-200 crash.

This demo takes a new player, records three crash games, and shows the recommendations change.

**Windows (PowerShell):**

```powershell
$u = "http://localhost:8000/users/999"
(Invoke-RestMethod "$u/recommendations").recommendations | Format-Table name, category, reason_source, reason

185, 190, 195 | ForEach-Object {
  Invoke-RestMethod -Method Post "$u/plays" -ContentType 'application/json' -Body "{`"game_id`": $_}" | Out-Null
}
(Invoke-RestMethod "$u/recommendations").recommendations | Format-Table name, category, reason_source, reason
```

**Linux / macOS (bash, requires `curl` and `jq`):**

```bash
u=http://localhost:8000/users/999
curl -s $u/recommendations | jq '.recommendations[] | {name, category, reason_source, reason}'

for id in 185 190 195; do
  curl -s -X POST $u/plays -H 'Content-Type: application/json' -d "{\"game_id\": $id}" > /dev/null
done
curl -s $u/recommendations | jq '.recommendations[] | {name, category, reason_source, reason}'
```

The first call returns the most popular game of each category. After three crash plays, crash games appear with template reasons. Call the endpoint again about a minute later and the reasons come from the LLM (`reason_source: "ai"`).

### Ports

| Port | Service |
|---|---|
| 8000 | API (REST, Swagger at `/docs`) |
| 6379 | Redis |
| 4566 | MiniStack (S3, DynamoDB) |
| 11434 | Ollama (only with the `ollama` profile) |

### Stop

```
docker compose down       # keep the Ollama model volume (seeded data is lost)
docker compose down -v    # also remove the Ollama model volume
```

## Testing

There is no automated test suite yet; it was out of scope for this exercise. Every path was verified by hand against the running stack:
- recommendations for players with 0, 1-2, 3-9 and 100+ plays;
- cache hits, and invalidation after a play;
- background AI reasons replacing template ones;
- the fallback to template reasons with the LLM unreachable, without an API key, and with Gemini returning `503`;
- the concurrency limit;
- the quality filter against real bad outputs.

The scoring and the quality filter are pure functions and would be the first candidates for unit tests.

## Design decisions

- **The LLM explains, it doesn't recommend.** Scoring is plain, deterministic Python: cheap, fast, explainable and testable. The LLM only turns facts the scorer already produced ("slot games are 70% of what they play; they often play Play'n GO") into a friendly sentence. The prompt contains only those facts, so the model has nothing else to base a claim on.
- **Background enrichment instead of a timeout.** The first design called the LLM during the login with a 5 s budget. Measurements killed it: `qwen2.5:1.5b` on CPU took 15-30 s, and Gemini's free tier took 30-45 s or returned `503`. Now the login responds in ~100 ms with template reasons, and a FastAPI background task upgrades the cached entry. Before writing, it checks that the cached entry is still the one it started from, so a play recorded in the meantime is never overwritten by stale recommendations.
- **One call, structured output, then a filter.** All 5 reasons come from one call validated against a JSON schema (`with_structured_output`). Small models still invented features, promised wins ("the odds are in your favor") or attached a reason to the wrong game. So each reason is rejected, and falls back to its template, if it mentions winning, odds, prizes or money, is too long, or describes another recommended game. In a gambling product, a missing flourish is better than an irresponsible sentence.
- **Blended scoring for cold start.** Instead of a separate branch for new players, the personal weight grows linearly with plays up to 10. One play of a slot doesn't turn the whole list into slots, and there's no sudden jump between "new" and "known" players.
- **Popularity on a log scale.** A few hit games get most of the plays; on a linear scale they would flatten every other game's popularity to about 0.
- **Provider-agnostic LLM.** LangChain's chat model interface lets `LLM_PROVIDER` switch between Gemini and Ollama with no code changes, and the Ollama container only starts under its own Compose profile.
- **Cloud-ready AWS access.** boto3 reads the standard `AWS_ENDPOINT_URL` variable. Locally it points to MiniStack; removing it targets real AWS with the same code.

## Scaling and next steps

Already in place:
- **LLM concurrency limit** (2 calls at a time, 90 s total timeout including the wait), so a login spike can't flood the model.
- **Bounded history reads.** Only the last 100 plays are read, however long a player's history is.
- **O(1) popularity.** A Redis counter is incremented on every play, instead of aggregating the play history.

Next steps:
- **Horizontal scaling.** The API is stateless (state lives in Redis and DynamoDB), so several instances can run behind a load balancer as they are. The background LLM calls would move to a queue (e.g. SQS) with dedicated workers, so they survive restarts and scale independently.
- **Concurrent logins.** Two simultaneous logins of the same player both compute the recommendations and both call the LLM. A short Redis lock (`SET NX`) would let the second one wait for the first.
- **Nightly precomputation.** A scheduled job (e.g. EventBridge + Lambda) could compute recommendations and AI reasons for recently active players, so almost every login is a cache hit with AI reasons ready.
- **Authentication.** User ids are numeric and there is no auth, so anyone can read any player's recommendations. In production the user id would come from the session token, not the URL.
