from datetime import datetime, timezone
from uuid import uuid4


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()


class UUIDGenerator:
    def new(self) -> str:
        return str(uuid4())
