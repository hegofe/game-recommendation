from datetime import datetime, timezone

from app import redis_keys
from app.clients import dynamodb, redis_client
from app.config import settings


def record_play(user_id: int, game_id: int) -> datetime:
    timestamp = datetime.now(timezone.utc)
    dynamodb().put_item(
        TableName=settings.plays_table,
        Item={
            "user_id": {"N": str(user_id)},
            "timestamp": {"S": timestamp.isoformat(timespec="microseconds")},
            "game_id": {"N": str(game_id)},
        },
    )
    r = redis_client()
    r.zincrby(redis_keys.POPULARITY, 1, game_id)
    r.delete(redis_keys.recommendations(user_id))
    return timestamp


def get_recent_plays(user_id: int, limit: int) -> list[tuple[int, datetime]]:
    """Returns (game_id, timestamp) pairs, newest first."""
    response = dynamodb().query(
        TableName=settings.plays_table,
        KeyConditionExpression="user_id = :uid",
        ExpressionAttributeValues={":uid": {"N": str(user_id)}},
        ScanIndexForward=False,
        Limit=limit,
    )
    return [
        (int(item["game_id"]["N"]), datetime.fromisoformat(item["timestamp"]["S"]))
        for item in response["Items"]
    ]
