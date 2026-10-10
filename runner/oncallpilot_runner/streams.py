"""The few Redis commands the runner uses, typed narrowly (all within the
`runner` ACL user's rules, architecture §2.7: streams, sorted sets, SET/GET/DEL/
EXPIRE/TTL/EXISTS, PING). Tests replace this class with an in-memory fake."""

from typing import Any, Protocol, cast

import redis

Entry = tuple[str, dict[str, str]]


class Streams(Protocol):
    def create_group(self, stream: str, group: str) -> None: ...
    def read_group(
        self, group: str, consumer: str, stream: str, last_id: str, count: int, block_ms: int | None
    ) -> list[Entry]: ...
    def autoclaim(
        self, stream: str, group: str, consumer: str, min_idle_ms: int, count: int
    ) -> list[Entry]: ...
    def ack(self, stream: str, group: str, entry_id: str) -> None: ...
    def add(self, stream: str, fields: dict[str, str]) -> str: ...
    def set_nx(self, key: str, ttl_seconds: int) -> bool: ...


class RedisStreams:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    @classmethod
    def from_url(cls, url: str) -> "RedisStreams":
        return cls(
            redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5,
                                 socket_timeout=15)
        )  # fmt: skip

    def create_group(self, stream: str, group: str) -> None:
        try:
            self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):  # the group exists already: fine
                raise

    def read_group(
        self, group: str, consumer: str, stream: str, last_id: str, count: int, block_ms: int | None
    ) -> list[Entry]:
        reply: Any = self.client.xreadgroup(
            group, consumer, {stream: last_id}, count=count, block=block_ms
        )
        return [
            (str(i), cast(dict[str, str], f)) for _, entries in (reply or []) for i, f in entries
            if f is not None
        ]  # fmt: skip

    def autoclaim(
        self, stream: str, group: str, consumer: str, min_idle_ms: int, count: int
    ) -> list[Entry]:
        reply: Any = self.client.xautoclaim(
            stream, group, consumer, min_idle_ms, start_id="0-0", count=count
        )
        return [(str(i), cast(dict[str, str], f)) for i, f in (reply[1] if reply else []) if f]

    def ack(self, stream: str, group: str, entry_id: str) -> None:
        self.client.xack(stream, group, entry_id)

    def add(self, stream: str, fields: dict[str, str]) -> str:
        return str(self.client.xadd(stream, cast(Any, fields)))

    def set_nx(self, key: str, ttl_seconds: int) -> bool:
        return bool(self.client.set(key, "1", nx=True, ex=ttl_seconds))
