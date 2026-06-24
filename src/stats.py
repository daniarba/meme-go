"""
stats.py — In-memory counters, sirf debug visibility ke liye (DB mein save nahi hote,
restart pe reset ho jate hain). Discord `!debug` command yahan se data dikhata hai.
"""
from collections import defaultdict

counters = defaultdict(int)
source_events = defaultdict(int)


def bump(key: str, amount: int = 1):
    counters[key] += amount


def bump_source(source: str):
    source_events[source] += 1


def snapshot() -> dict:
    return {"counters": dict(counters), "source_events": dict(source_events)}
