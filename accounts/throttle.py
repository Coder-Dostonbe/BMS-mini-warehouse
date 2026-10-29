"""Login brute-force guard: 5 failures per (login, IP) -> 5 minute block."""

import hashlib

from django.core.cache import cache

MAX_FAILURES = 5
BLOCK_SECONDS = 5 * 60


def _key(username: str, ip: str) -> str:
    raw = f"{username.strip().casefold()}|{ip}".encode()
    return "login-failures:" + hashlib.sha256(raw).hexdigest()


def is_blocked(username: str, ip: str) -> bool:
    return cache.get(_key(username, ip), 0) >= MAX_FAILURES


def register_failure(username: str, ip: str) -> int:
    key = _key(username, ip)
    if cache.add(key, 1, BLOCK_SECONDS):
        return 1
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, BLOCK_SECONDS)
        return 1
    # Sliding window: the block lasts BLOCK_SECONDS after the last failure.
    cache.touch(key, BLOCK_SECONDS)
    return count


def reset(username: str, ip: str) -> None:
    cache.delete(_key(username, ip))
