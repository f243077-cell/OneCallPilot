"""An in-memory Streams (consumer groups, pending entries, idle times, SET NX)."""

from dataclasses import dataclass, field

from oncallpilot_runner.streams import Entry


@dataclass
class Pending:
    consumer: str
    delivered_ms: int


@dataclass
class FakeStreams:
    now_ms: int = 1_000_000
    streams: dict[str, list[Entry]] = field(default_factory=dict)
    last_delivered: dict[tuple[str, str], int] = field(default_factory=dict)
    pending: dict[tuple[str, str], dict[str, Pending]] = field(default_factory=dict)
    keys: dict[str, int] = field(default_factory=dict)  # key -> expiry ms
    counter: int = 0

    def create_group(self, stream: str, group: str) -> None:
        self.streams.setdefault(stream, [])
        self.last_delivered.setdefault((stream, group), 0)
        self.pending.setdefault((stream, group), {})

    def read_group(
        self, group: str, consumer: str, stream: str, last_id: str, count: int, block_ms: int | None
    ) -> list[Entry]:
        entries = self.streams.get(stream, [])
        pending = self.pending[(stream, group)]
        if last_id == "0":  # this consumer's own pending entries
            own = [e for e in entries if e[0] in pending and pending[e[0]].consumer == consumer]
            return own[:count]
        start = self.last_delivered[(stream, group)]
        batch = entries[start : start + count]
        self.last_delivered[(stream, group)] = start + len(batch)
        for entry_id, _ in batch:
            pending[entry_id] = Pending(consumer, self.now_ms)
        return batch

    def autoclaim(
        self, stream: str, group: str, consumer: str, min_idle_ms: int, count: int
    ) -> list[Entry]:
        pending = self.pending[(stream, group)]
        claimed: list[Entry] = []
        for entry_id, fields in self.streams.get(stream, []):
            p = pending.get(entry_id)
            if p and self.now_ms - p.delivered_ms >= min_idle_ms and len(claimed) < count:
                pending[entry_id] = Pending(consumer, self.now_ms)
                claimed.append((entry_id, fields))
        return claimed

    def ack(self, stream: str, group: str, entry_id: str) -> None:
        self.pending[(stream, group)].pop(entry_id, None)

    def add(self, stream: str, fields: dict[str, str]) -> str:
        self.counter += 1
        entry_id = f"{self.now_ms}-{self.counter}"
        self.streams.setdefault(stream, []).append((entry_id, dict(fields)))
        return entry_id

    def set_nx(self, key: str, ttl_seconds: int) -> bool:
        expiry = self.keys.get(key)
        if expiry is not None and expiry > self.now_ms:
            return False
        self.keys[key] = self.now_ms + ttl_seconds * 1000
        return True
