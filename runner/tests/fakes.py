"""In-memory fakes: Streams (consumer groups, pending entries, idle times, SET NX,
the limit counters) and FakeDockerClient (containers, with every call recorded)."""

from dataclasses import dataclass, field, replace

from oncallpilot_runner.docker_client import Container, DockerUnavailable
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
    zsets: dict[str, dict[str, float]] = field(default_factory=dict)
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

    def zcount(self, key: str, min_score: float, max_score: float) -> int:
        return sum(
            1 for score in self.zsets.get(key, {}).values() if min_score <= score <= max_score
        )

    def exists(self, key: str) -> bool:
        expiry = self.keys.get(key)
        return expiry is not None and expiry > self.now_ms


WRITE_CALLS = frozenset({"create", "start", "stop", "restart", "kill", "remove"})


@dataclass
class FakeDockerClient:
    """Docker as seen through socket-proxy-rw, in memory.

    `calls` records every call, reads included. The write methods exist (and
    change state) only so a test can prove a dry run never reaches them: a dry
    run must leave `calls` without any of WRITE_CALLS and `containers` unchanged.
    """

    containers: dict[str, Container] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    unavailable: bool = False

    def add(self, name: str, service: str, release: str, replica: int, state: str = "created",
            health: str = "none", **labels: str) -> None:  # fmt: skip
        base = {
            "oncallpilot.managed": "true",
            "oncallpilot.service": service,
            "oncallpilot.release": release,
            "oncallpilot.replica": str(replica),
        }
        self.containers[name] = Container(name, base | labels, state, health)

    def snapshot(self) -> dict[str, Container]:
        return dict(self.containers)

    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] in WRITE_CALLS]

    def list_containers(self, labels: dict[str, str]) -> list[Container]:
        self.calls.append(("list", ",".join(f"{k}={v}" for k, v in sorted(labels.items()))))
        if self.unavailable:
            raise DockerUnavailable("Docker did not answer: ConnectionError")
        return [
            c for c in self.containers.values()
            if all(c.labels.get(k) == v for k, v in labels.items())
        ]  # fmt: skip

    def create(self, name: str) -> None:
        self.calls.append(("create", name))

    def start(self, name: str) -> None:
        self.calls.append(("start", name))
        self.containers[name] = replace(self.containers[name], state="running")

    def stop(self, name: str) -> None:
        self.calls.append(("stop", name))
        self.containers[name] = replace(self.containers[name], state="exited")

    def restart(self, name: str) -> None:
        self.calls.append(("restart", name))

    def kill(self, name: str) -> None:
        self.calls.append(("kill", name))
        self.containers[name] = replace(self.containers[name], state="exited")

    def remove(self, name: str) -> None:
        self.calls.append(("remove", name))
        del self.containers[name]
