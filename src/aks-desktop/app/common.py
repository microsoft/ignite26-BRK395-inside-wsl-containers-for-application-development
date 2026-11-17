"""Small helpers shared by the seeder and benchmark workers."""
import json
import os
import threading
import time
import urllib.request

REPORT_URL = os.environ.get("REPORT_URL", "http://arena-dashboard.arena.svc.cluster.local/api/report")
RUN_ID = os.environ.get("RUN_ID", "")
JOB_NAME = os.environ.get("JOB_NAME", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://arena:arena@arena-postgres:5432/arena")


class Reporter:
    """Pushes progress updates to the dashboard (best effort, throttled)."""

    def __init__(self, key: str, min_interval: float = 0.4):
        self.key = key
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def send(self, force: bool = False, **payload):
        now = time.time()
        with self._lock:
            if not force and now - self._last < self.min_interval:
                return
            self._last = now
        body = json.dumps({"run_id": RUN_ID, "job": JOB_NAME, "key": self.key, "ts": now, **payload}).encode()
        req = urllib.request.Request(REPORT_URL, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=3).read()
        except Exception as exc:  # dashboard may be restarting; never fail the benchmark for it
            print(f"[report] {exc}", flush=True)
        print(f"[{self.key}] {payload.get('phase')} {payload.get('progress', '')} {payload.get('message', '')}", flush=True)


def connect(application_name: str, attempts: int = 60):
    import psycopg

    last = None
    for _ in range(attempts):
        try:
            return psycopg.connect(DATABASE_URL, application_name=application_name, autocommit=True)
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError(f"could not connect to database: {last}")
