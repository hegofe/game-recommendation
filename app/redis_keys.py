POPULARITY = "popularity"
RECOMMENDATIONS_PATTERN = "reco:*"


def recommendations(user_id: int) -> str:
    return f"reco:{user_id}"
