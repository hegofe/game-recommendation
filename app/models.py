from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Game(BaseModel):
    game_id: int
    name: str
    category: str
    provider: str


class PlayCreate(BaseModel):
    game_id: int = Field(gt=0)


class Play(Game):
    timestamp: datetime


class Recommendation(Game):
    score: float


class ExplainedRecommendation(Recommendation):
    reason: str
    reason_source: Literal["ai", "template"]


class RecommendationsResponse(BaseModel):
    user_id: int
    generated_at: datetime
    recommendations: list[ExplainedRecommendation]
