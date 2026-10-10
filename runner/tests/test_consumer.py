"""The consumer loop on ocp:runner:requests (RUN-001, RUN-015, NFR-005)."""

import json
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from oncallpilot_runner.checks import Accepted, Checker, Dropped, Duplicate
from oncallpilot_runner.consumer import (
    RECLAIM_IDLE_MS,
    REQUESTS_GROUP,
    REQUESTS_STREAM,
    RESULTS_STREAM,
    Consumer,
)
from oncallpilot_runner.signing import Keys, verify
from tests.conftest import NOW
from tests.fakes import FakeStreams
from tests.publish_signed import build_request, entry, signed_for


@pytest.fixture
def consumer(streams: FakeStreams, checker: Checker) -> Consumer:
    c = Consumer(streams, checker, "runner-a", heartbeat=None)
    c.ensure_group()
    return c


def results(streams: FakeStreams) -> list[dict[str, Any]]:
    return [json.loads(f["msg"]) for _, f in streams.streams.get(RESULTS_STREAM, [])]


def pending(streams: FakeStreams) -> dict[str, Any]:
    return streams.pending[(REQUESTS_STREAM, REQUESTS_GROUP)]


def publish(streams: FakeStreams, message: dict[str, Any]) -> str:
    return streams.add(REQUESTS_STREAM, entry(message))


def test_a_refusal_is_published_signed_and_the_request_acknowledged(
    consumer: Consumer, streams: FakeStreams, keys: Keys
) -> None:
    forged = {**build_request(now=NOW), "sig": "0" * 64}
    publish(streams, forged)
    assert consumer.poll() == 1
    (result,) = results(streams)
    assert result["status"] == "refused" and result["error_code"] == "BAD_SIGNATURE"
    assert result["execution_id"] == forged["execution_id"]
    assert verify(result, keys.link)
    assert pending(streams) == {}


def test_accepted_duplicate_and_dropped_publish_nothing_and_are_acknowledged(
    consumer: Consumer, streams: FakeStreams, keys: Keys
) -> None:
    good = signed_for(build_request(now=NOW), keys)
    publish(streams, good)
    publish(streams, good)  # a replay
    streams.add(REQUESTS_STREAM, {"msg": "not json"})
    decisions = []
    for entry_id, fields in streams.read_group(
        REQUESTS_GROUP, "runner-a", REQUESTS_STREAM, ">", 10, None
    ):
        decisions.append(consumer.handle(entry_id, fields))
    assert [type(d) for d in decisions] == [Accepted, Duplicate, Dropped]
    assert results(streams) == []
    assert pending(streams) == {}


def test_a_restarted_runner_reads_its_own_pending_entries_first(
    streams: FakeStreams, checker: Checker, keys: Keys
) -> None:
    first = Consumer(streams, checker, "runner-a", heartbeat=None)
    first.ensure_group()
    publish(streams, signed_for(build_request(now=NOW), keys))
    publish(streams, {**build_request(now=NOW), "sig": "1" * 64})
    # Delivered to runner-a, which "crashes" before handling them.
    streams.read_group(REQUESTS_GROUP, "runner-a", REQUESTS_STREAM, ">", 10, None)
    assert len(pending(streams)) == 2
    restarted = Consumer(streams, checker, "runner-a", heartbeat=None)
    assert restarted.recover() == 2
    assert pending(streams) == {}
    assert [r["error_code"] for r in results(streams)] == ["BAD_SIGNATURE"]


def test_entries_idle_over_300_s_are_reclaimed_from_another_consumer(
    streams: FakeStreams, checker: Checker, keys: Keys
) -> None:
    dead = Consumer(streams, checker, "runner-old", heartbeat=None)
    dead.ensure_group()
    publish(streams, {**build_request(now=NOW), "sig": "2" * 64})
    streams.read_group(REQUESTS_GROUP, "runner-old", REQUESTS_STREAM, ">", 10, None)
    alive = Consumer(streams, checker, "runner-new", heartbeat=None)
    streams.now_ms += RECLAIM_IDLE_MS - 1
    assert alive.reclaim() == 0  # not idle long enough: never taken over
    streams.now_ms += 1
    assert alive.reclaim() == 1
    assert pending(streams) == {} and len(results(streams)) == 1


def test_a_crash_between_result_and_ack_repeats_the_result_not_the_action(
    streams: FakeStreams, checker: Checker, keys: Keys
) -> None:
    consumer = Consumer(streams, checker, "runner-a", heartbeat=None)
    consumer.ensure_group()
    good = signed_for(build_request(now=NOW), keys)
    entry_id = publish(streams, good)
    fields = streams.read_group(REQUESTS_GROUP, "runner-a", REQUESTS_STREAM, ">", 10, None)[0][1]
    assert isinstance(checker.check(fields), Accepted)  # handled, but "crashed" before XACK
    assert entry_id in pending(streams)
    assert isinstance(consumer.handle(entry_id, fields), Duplicate)  # redelivery: not run again


def test_a_bug_in_a_check_still_answers_with_a_refusal(
    consumer: Consumer,
    streams: FakeStreams,
    checker: Checker,
    keys: Keys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_: dict[str, str]) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(checker, "check", boom)
    message = signed_for(build_request(now=NOW), keys)
    publish(streams, message)
    consumer.poll()
    (result,) = results(streams)
    assert result["status"] == "refused" and result["error_code"] == "VALIDATION_ERROR"
    assert pending(streams) == {}


def test_redis_errors_are_retried_with_backoff_and_never_escape(
    streams: FakeStreams, checker: Checker
) -> None:
    calls = {"n": 0}
    waits: list[float] = []

    class Flaky(FakeStreams):
        def create_group(self, stream: str, group: str) -> None:
            calls["n"] += 1
            if calls["n"] <= 3:
                raise RedisConnectionError("down")
            super().create_group(stream, group)

    class Stop(Exception):
        pass

    def sleep(seconds: float) -> None:
        waits.append(seconds)

    flaky = Flaky()
    consumer = Consumer(flaky, checker, "runner-a", heartbeat=None, sleep=sleep)

    def stop_after_first_poll(*_: Any, **__: Any) -> int:
        raise Stop

    consumer.poll = stop_after_first_poll  # type: ignore[method-assign]
    with pytest.raises(Stop):
        consumer.run_forever()
    assert waits == [1.0, 2.0, 4.0]


def test_the_heartbeat_is_written_after_each_poll(
    streams: FakeStreams, checker: Checker, tmp_path: Any
) -> None:
    beat = tmp_path / "beat"
    consumer = Consumer(streams, checker, "runner-a", heartbeat=beat)
    consumer.ensure_group()
    consumer.poll()
    assert float(beat.read_text(encoding="utf-8")) > 0


def test_refusals_echo_the_request_ids(
    consumer: Consumer, streams: FakeStreams, keys: Keys
) -> None:
    dry = signed_for(build_request("dry_run", "exec_shell", {"cmd": "id"}, now=NOW), keys)
    publish(streams, dry)
    consumer.poll()
    (result,) = results(streams)
    assert (result["type"], result["request_id"], result["execution_id"]) == (
        "dry_run_result", dry["request_id"], None,
    )  # fmt: skip
