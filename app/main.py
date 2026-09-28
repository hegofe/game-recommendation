import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, HTTPException, Path, Query

from app import recommendations
from app.catalog import get_catalog
from app.config import settings
from app.models import Play, PlayCreate, RecommendationsResponse
from app.plays import get_recent_plays, record_play

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Catalog loaded: %d games", len(get_catalog()))
    except Exception:
        logger.warning("Catalog not available yet (run the seed); it will be loaded on first request")
    yield


app = FastAPI(
    title="Game Recommendation API",
    description="Recommends casino games to each user at login.",
    version="0.1.0",
    lifespan=lifespan,
)

UserId = Annotated[int, Path(gt=0)]


@app.get("/users/{user_id}/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(user_id: UserId, background_tasks: BackgroundTasks):
    response, profile = await asyncio.to_thread(recommendations.get_or_create, user_id)
    if profile is not None and profile.num_plays > 0:
        background_tasks.add_task(recommendations.add_ai_reasons, response, profile)
    return response


@app.post("/users/{user_id}/plays", response_model=Play, status_code=201)
def create_play(user_id: UserId, body: PlayCreate):
    game = get_catalog().get(body.game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {body.game_id} not found")
    timestamp = record_play(user_id, game.game_id)
    return Play(**game.model_dump(), timestamp=timestamp)


@app.get("/users/{user_id}/plays", response_model=list[Play])
def list_plays(user_id: UserId, limit: Annotated[int, Query(ge=1, le=1000)] = settings.history_limit):
    catalog = get_catalog()
    return [
        Play(**catalog[game_id].model_dump(), timestamp=timestamp)
        for game_id, timestamp in get_recent_plays(user_id, limit)
        if game_id in catalog
    ]
