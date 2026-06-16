"""ریت‌لیمیتر ساده‌ی per-user (پنجره‌ی لغزان در حافظه)."""
from __future__ import annotations

import time
from collections import defaultdict, deque

_hits: dict[int, deque[float]] = defaultdict(deque)


def allow(user_id: int, max_hits: int, window_seconds: int) -> bool:
    """اگر کاربر در پنجره‌ی زمانی از سقف عبور نکرده باشد True برمی‌گرداند."""
    now = time.monotonic()
    q = _hits[user_id]
    cutoff = now - window_seconds
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= max_hits:
        return False
    q.append(now)
    return True
