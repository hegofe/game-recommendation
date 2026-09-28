from functools import cache

import boto3
import redis

from app.config import settings


@cache
def s3():
    return boto3.client("s3")


@cache
def dynamodb():
    return boto3.client("dynamodb")


@cache
def redis_client() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)
