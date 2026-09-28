import asyncio
from datetime import datetime, timezone

from app import redis_keys
from app.clients import redis_client
from app.config import settings
from app.explainer import ai_reasons, template_reason
from app.models import ExplainedRecommendation, RecommendationsResponse
from app.recommender import UserProfile, build_profile, recommend


def get_or_create(user_id: int) -> tuple[RecommendationsResponse, UserProfile | None]:
    """Returns the cached recommendations, or computes them with template reasons and caches them.

    The profile is returned only when the recommendations were just computed.
    """
    key = redis_keys.recommendations(user_id)
    cached = redis_client().get(key)
    if cached:
        return RecommendationsResponse.model_validate_json(cached), None

    profile = build_profile(user_id)
    response = RecommendationsResponse(
        user_id=user_id,
        generated_at=datetime.now(timezone.utc),
        recommendations=[
            ExplainedRecommendation(**r.model_dump(), reason=template_reason(r, profile), reason_source="template")
            for r in recommend(profile)
        ],
    )
    redis_client().set(key, response.model_dump_json(), ex=settings.reco_cache_ttl_seconds)
    return response, profile


async def add_ai_reasons(response: RecommendationsResponse, profile: UserProfile) -> None:
    """Background task: replaces template reasons with LLM ones in the cached recommendations."""
    reasons = await ai_reasons(response.recommendations, profile)
    if not reasons:
        return
    updated = response.model_copy(update={"recommendations": [
        r.model_copy(update={"reason": reasons[r.game_id], "reason_source": "ai"}) if r.game_id in reasons else r
        for r in response.recommendations
    ]})
    await asyncio.to_thread(_replace_if_current, updated)


def _replace_if_current(updated: RecommendationsResponse) -> None:
    # Skip if the entry was invalidated (new play) or recomputed while the LLM was running.
    key = redis_keys.recommendations(updated.user_id)
    cached = redis_client().get(key)
    if cached and RecommendationsResponse.model_validate_json(cached).generated_at == updated.generated_at:
        redis_client().set(key, updated.model_dump_json(), xx=True, keepttl=True)
