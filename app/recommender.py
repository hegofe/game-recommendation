import math
from collections import Counter
from dataclasses import dataclass

from app import redis_keys
from app.catalog import get_catalog
from app.clients import redis_client
from app.config import settings
from app.models import Game, Recommendation
from app.plays import get_recent_plays

CATEGORY_WEIGHT = 0.7
PROVIDER_WEIGHT = 0.3


@dataclass
class UserProfile:
    user_id: int
    num_plays: int
    categories: dict[str, float]
    providers: dict[str, float]
    played_game_ids: list[int]
    favorite_game_ids: list[int]

    @property
    def personal_weight(self) -> float:
        return min(self.num_plays / settings.full_personalization_plays, 1.0)


def build_profile(user_id: int) -> UserProfile:
    catalog = get_catalog()
    game_ids = [g for g, _ in get_recent_plays(user_id, settings.history_limit) if g in catalog]
    n = len(game_ids)
    categories = Counter(catalog[g].category for g in game_ids)
    providers = Counter(catalog[g].provider for g in game_ids)
    return UserProfile(
        user_id=user_id,
        num_plays=n,
        categories={c: count / n for c, count in categories.items()},
        providers={p: count / n for p, count in providers.items()},
        played_game_ids=list(dict.fromkeys(game_ids)),
        favorite_game_ids=[g for g, _ in Counter(game_ids).most_common(3)],
    )


def _popularity() -> dict[int, float]:
    """Play counts scaled to 0-1 on a log scale, so a few hits don't flatten the rest."""
    counts = {int(g): c for g, c in redis_client().zrange(redis_keys.POPULARITY, 0, -1, withscores=True)}
    top = max(counts.values(), default=0)
    if top <= 0:
        return {}
    return {g: math.log1p(c) / math.log1p(top) for g, c in counts.items()}


def recommend(profile: UserProfile) -> list[Recommendation]:
    popularity = _popularity()
    played = set(profile.played_game_ids)
    w = profile.personal_weight

    ranked = []
    for game in get_catalog().values():
        if game.game_id in played:
            continue
        pop = popularity.get(game.game_id, 0.0)
        affinity = (CATEGORY_WEIGHT * profile.categories.get(game.category, 0.0)
                    + PROVIDER_WEIGHT * profile.providers.get(game.provider, 0.0))
        ranked.append((w * affinity + (1 - w) * pop, pop, game))
    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)

    return _select([(score, game) for score, _, game in ranked], distinct_categories=profile.num_plays == 0)


def _select(ranked: list[tuple[float, Game]], distinct_categories: bool) -> list[Recommendation]:
    k = settings.num_recommendations
    selected: list[Recommendation] = []
    chosen: set[int] = set()
    categories: set[str] = set()
    per_provider: Counter[str] = Counter()

    # With distinct_categories, a first pass takes one game per category and a second pass fills the rest.
    for enforce_categories in ([True, False] if distinct_categories else [False]):
        for score, game in ranked:
            if len(selected) == k:
                return selected
            if game.game_id in chosen or per_provider[game.provider] >= settings.max_per_provider:
                continue
            if enforce_categories and game.category in categories:
                continue
            selected.append(Recommendation(**game.model_dump(), score=round(score, 4)))
            chosen.add(game.game_id)
            categories.add(game.category)
            per_provider[game.provider] += 1
    return selected
