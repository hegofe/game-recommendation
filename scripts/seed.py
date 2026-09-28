import json
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

import boto3
import redis

from app import redis_keys
from app.config import settings

rng = random.Random(42)

PROVIDERS = [
    "NetEnt", "Pragmatic Play", "Play'n GO", "Evolution",
    "Microgaming", "Novomatic", "Playtech", "Red Tiger",
]

CATEGORY_PROVIDERS = {
    "slots": ["NetEnt", "Pragmatic Play", "Play'n GO", "Microgaming", "Novomatic", "Red Tiger"],
    "roulette": ["Evolution", "Playtech", "NetEnt", "Microgaming"],
    "blackjack": ["Evolution", "Playtech", "Pragmatic Play", "Microgaming"],
    "live": ["Evolution", "Pragmatic Play", "Playtech"],
    "crash": ["Pragmatic Play", "Red Tiger", "Play'n GO"],
}

CATEGORY_WEIGHTS = {"slots": 5, "live": 2, "roulette": 1, "blackjack": 1, "crash": 1}


def _names() -> dict[str, list[str]]:
    slot_prefixes = ["Book of", "Legacy of", "Rise of", "Temple of", "Gates of",
                     "Fortune of", "Wild", "Mega", "Golden", "Lucky"]
    slot_themes = ["Ra", "Olympus", "Dragons", "Vikings", "Aztecs", "Fruits",
                   "Pirates", "Wolves", "Gems", "Pharaohs", "Zeus", "Samurai"]
    table_styles = ["Classic", "European", "French", "American", "Mini", "Speed",
                    "VIP", "Immersive", "Premium", "Double"]
    live_shows = ["Wheel of Fortune", "Money Drop", "Treasure Hunt", "Dice Party",
                  "Card Duel", "Baccarat Squeeze", "Lucky Balls", "Poker Night",
                  "Sic Bo", "Coin Flip"]
    crash_games = ["Rocket", "Balloon", "Moonshot", "Jet", "Comet",
                   "Meteor", "Turbo", "Skyline", "Orbit", "Lift Off"]

    slots = [f"{p} {t}" for p in slot_prefixes for t in slot_themes]
    return {
        "slots": rng.sample(slots, 110),
        "roulette": [f"{s} Roulette{v}" for s in table_styles for v in ("", " Pro")],
        "blackjack": [f"{s} Blackjack{v}" for s in table_styles for v in ("", " Pro")],
        "live": [f"{s}{v}" for s in live_shows for v in (" Live", " Live VIP", " Live Deluxe")],
        "crash": [f"{s}{v}" for s in crash_games for v in ("", " X")],
    }


def generate_catalog() -> list[dict]:
    catalog = []
    for category, names in _names().items():
        for name in names:
            catalog.append({
                "game_id": len(catalog) + 1,
                "name": name,
                "category": category,
                "provider": rng.choice(CATEGORY_PROVIDERS[category]),
            })
    return catalog


def _plays_count(user_id: int) -> int:
    if user_id <= 5:
        return rng.randint(1, 2)
    if user_id <= 10:
        return rng.randint(3, 9)
    return rng.randint(10, 200)


def generate_plays(catalog: list[dict], num_users: int = 50) -> list[dict]:
    # Some games are much more popular than others, so global popularity is meaningful.
    base_weight = {g["game_id"]: rng.paretovariate(1.5) for g in catalog}
    categories = list(CATEGORY_WEIGHTS)
    now = datetime.now(timezone.utc)

    plays = []
    for user_id in range(1, num_users + 1):
        fav_category = rng.choices(categories, weights=list(CATEGORY_WEIGHTS.values()))[0]
        fav_provider = rng.choice(CATEGORY_PROVIDERS[fav_category])
        weights = []
        for g in catalog:
            w = base_weight[g["game_id"]]
            if g["category"] == fav_category:
                w *= 8
            if g["provider"] == fav_provider:
                w *= 3
            weights.append(w)

        timestamps = set()
        n = _plays_count(user_id)
        while len(timestamps) < n:
            ts = now - timedelta(seconds=rng.randint(0, 90 * 24 * 3600), microseconds=rng.randint(0, 999_999))
            timestamps.add(ts.isoformat())

        games = rng.choices(catalog, weights=weights, k=n)
        for ts, game in zip(sorted(timestamps), games):
            plays.append({"user_id": user_id, "timestamp": ts, "game_id": game["game_id"]})
    return plays


def upload_catalog(catalog: list[dict]) -> None:
    s3 = boto3.client("s3")
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if settings.s3_bucket not in existing:
        s3.create_bucket(Bucket=settings.s3_bucket)
    s3.put_object(
        Bucket=settings.s3_bucket,
        Key=settings.catalog_key,
        Body=json.dumps(catalog, indent=2).encode(),
        ContentType="application/json",
    )


def load_plays(plays: list[dict]) -> None:
    dynamodb = boto3.resource("dynamodb")
    client = dynamodb.meta.client
    if settings.plays_table in client.list_tables()["TableNames"]:
        client.delete_table(TableName=settings.plays_table)
        client.get_waiter("table_not_exists").wait(TableName=settings.plays_table)

    table = dynamodb.create_table(
        TableName=settings.plays_table,
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "N"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()

    with table.batch_writer() as batch:
        for play in plays:
            batch.put_item(Item=play)


def init_redis(plays: list[dict]) -> None:
    r = redis.from_url(settings.redis_url)
    r.delete(redis_keys.POPULARITY)
    for key in r.scan_iter(redis_keys.RECOMMENDATIONS_PATTERN):
        r.delete(key)
    counts = Counter(p["game_id"] for p in plays)
    r.zadd(redis_keys.POPULARITY, dict(counts))


def main() -> None:
    catalog = generate_catalog()
    plays = generate_plays(catalog)

    upload_catalog(catalog)
    print(f"S3: {len(catalog)} games -> s3://{settings.s3_bucket}/{settings.catalog_key}")

    load_plays(plays)
    users = len({p["user_id"] for p in plays})
    print(f"DynamoDB: {len(plays)} plays from {users} users -> table '{settings.plays_table}'")

    init_redis(plays)
    print(f"Redis: popularity initialized for {len({p['game_id'] for p in plays})} games")


if __name__ == "__main__":
    main()
