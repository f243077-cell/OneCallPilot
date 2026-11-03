"""The helper container's output reaches the CLI whatever the daemon's default
logging driver is (docker-py returns None for drivers other than json-file
and journald; Docker Desktop can default to `local`)."""

import json
from typing import Any

import pytest

from chaos_cli.docker_ops import Docker, DockerOpsError


class FakeContainers:
    def __init__(self, output: bytes | None) -> None:
        self.output = output
        self.kwargs: dict[str, Any] = {}

    def run(self, image: str, **kwargs: Any) -> bytes | None:
        self.kwargs = kwargs
        return self.output


class FakeClient:
    def __init__(self, output: bytes | None) -> None:
        self.containers = FakeContainers(output)


def test_the_helper_logs_with_json_file_so_its_output_can_be_read() -> None:
    client = FakeClient(json.dumps({"results": [1]}).encode())
    assert Docker(client).run_helper([{"op": "x"}], "token") == [1]
    assert client.containers.kwargs["log_config"]["Type"] == "json-file"
    assert client.containers.kwargs["remove"] is True


def test_no_output_is_a_clear_error_not_a_type_error() -> None:
    with pytest.raises(DockerOpsError, match="no output"):
        Docker(FakeClient(None)).run_helper([{"op": "x"}], "token")
