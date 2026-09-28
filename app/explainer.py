import asyncio
import logging
import re
from functools import cache

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from pydantic import BaseModel

from app.catalog import get_catalog
from app.config import settings
from app.models import Game
from app.recommender import UserProfile

logger = logging.getLogger("uvicorn.error")

CATEGORY_LABELS = {
    "slots": "slot",
    "roulette": "roulette",
    "blackjack": "blackjack",
    "live": "live casino",
    "crash": "crash",
}

PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You write recommendation reasons for an online casino. "
     "For each game, write ONE short friendly sentence (max 15 words) addressed to the player as 'you'. "
     "Use ONLY the facts given for that game. Never mention winning, odds, volatility, prizes or features."),
    ("human", "The player's most played games: {favorites}\n\nRecommended games:\n{games}"),
])

FORBIDDEN_WORDS = re.compile(
    r"\b(win\w*|odds|jackpot\w*|prize\w*|payout\w*|guarante\w*|volatil\w*|rtp|bonus\w*|money|cash|rich)\b",
    re.IGNORECASE,
)


class _Reason(BaseModel):
    game_id: int
    reason: str


class _Reasons(BaseModel):
    reasons: list[_Reason]


_llm_slots = asyncio.Semaphore(settings.llm_max_concurrency)


def _build_llm() -> BaseChatModel:
    if settings.llm_provider == "ollama":
        return ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=0.3,
            num_predict=300,
        )
    if not settings.google_api_key or not settings.google_api_key.get_secret_value():
        raise RuntimeError("GOOGLE_API_KEY is not set")
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        api_key=settings.google_api_key,
        max_retries=2,
    )


@cache
def _chain() -> Runnable:
    return PROMPT | _build_llm().with_structured_output(_Reasons)


def template_reason(game: Game, profile: UserProfile) -> str:
    label = CATEGORY_LABELS.get(game.category, game.category)
    if profile.num_plays == 0:
        return f"One of the most popular {label} games right now."
    likes_category = game.category in profile.categories
    likes_provider = game.provider in profile.providers
    if likes_category and likes_provider:
        return f"You enjoy {label} games from {game.provider}."
    if likes_category:
        return f"Because you enjoy {label} games."
    if likes_provider:
        return f"More from {game.provider}, a provider you play often."
    return f"A popular {label} game to try something new."


def _facts(game: Game, profile: UserProfile) -> str:
    label = CATEGORY_LABELS.get(game.category, game.category)
    facts = []
    if game.category in profile.categories:
        facts.append(f"{label} games are {profile.categories[game.category]:.0%} of what they play")
    if game.provider in profile.providers:
        facts.append(f"they often play games by {game.provider}")
    if not facts:
        facts.append("popular with other players, something new for them")
    return "; ".join(facts)


async def _llm_reasons(games: list[Game], profile: UserProfile) -> dict[int, str]:
    catalog = get_catalog()
    result = await _chain().ainvoke({
        "favorites": ", ".join(catalog[g].name for g in profile.favorite_game_ids if g in catalog),
        "games": "\n".join(
            f"- game_id {g.game_id}: {g.name} ({g.provider}). Facts: {_facts(g, profile)}." for g in games
        ),
    })
    by_id = {g.game_id: g for g in games}
    return {
        r.game_id: r.reason.strip()
        for r in result.reasons
        if r.game_id in by_id and _is_acceptable(r.reason.strip(), by_id[r.game_id], games)
    }


def _is_acceptable(reason: str, game: Game, games: list[Game]) -> bool:
    """Rejects empty or long reasons, gambling promises, and reasons that describe another recommended game."""
    if not reason or len(reason) > 160:
        return False
    text = reason.lower().replace(game.name.lower(), "")
    if FORBIDDEN_WORDS.search(text):
        return False
    return not any(g.name.lower() in text for g in games if g.game_id != game.game_id)


async def _ai_reasons_limited(games: list[Game], profile: UserProfile) -> dict[int, str]:
    async with _llm_slots:
        return await _llm_reasons(games, profile)


async def ai_reasons(games: list[Game], profile: UserProfile) -> dict[int, str]:
    """Returns the accepted LLM reasons by game_id; empty if the LLM fails or times out (queue wait included)."""
    try:
        reasons = await asyncio.wait_for(_ai_reasons_limited(games, profile), settings.llm_timeout_seconds)
        logger.info("LLM reasons for user %d: %d/%d accepted", profile.user_id, len(reasons), len(games))
        return reasons
    except Exception as e:
        logger.warning("LLM failed for user %d, keeping template reasons: %r", profile.user_id, e)
        return {}
