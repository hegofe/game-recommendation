import json
from functools import cache

from app.clients import s3
from app.config import settings
from app.models import Game


@cache
def get_catalog() -> dict[int, Game]:
    body = s3().get_object(Bucket=settings.s3_bucket, Key=settings.catalog_key)["Body"].read()
    return {g["game_id"]: Game(**g) for g in json.loads(body)}
