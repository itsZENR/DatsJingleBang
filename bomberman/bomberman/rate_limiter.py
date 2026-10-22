"""
Ограничитель частоты запросов (rate limiting)
"""

import time
import threading
from collections import deque


class RateLimiter:
    """Ограничитель частоты запросов: макс 3 req/sec"""

    def __init__(self, max_requests: int = 3, per_seconds: float = 1.0):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.requests = deque()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            while self.requests and self.requests[0] < now - self.per_seconds:
                self.requests.popleft()
            if len(self.requests) >= self.max_requests:
                sleep_time = self.requests[0] + self.per_seconds - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
                if self.requests:
                    self.requests.popleft()
            self.requests.append(time.time())
