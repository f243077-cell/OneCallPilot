"""The runner's access to the deploy ledger (A2.4a ruling of 2026-10-11, live).

After `chaos reset`, the chaos CLI's helper leaves `/ledger` owned by root (mode
0755) and `deploys.jsonl` owned by uid 10001 (mode 0644). The runner image, run as
its own user (uid 10001) with the volume mounted read-write, every capability
dropped and a read-only root, must be able to append to the file and nothing
else: creating, renaming or deleting in the directory is refused.

Known, accepted gap (week-3.md, A2.4a): a `chaos reset` that lands in the
middle of a runner append loses that one record.
"""

import json

import pytest

from tests.live.stack import LEDGER_VOLUME, chaos, ledger_text
from tests.live.test_live_runner import IMAGE, docker

pytestmark = pytest.mark.live

PROBE = r"""
import json, os
path, d = "/ledger/deploys.jsonl", "/ledger"
out = {"uid": os.getuid()}
st_dir, st_file = os.stat(d), os.stat(path)
out["dir"] = [st_dir.st_uid, oct(st_dir.st_mode & 0o777)]
out["file"] = [st_file.st_uid, oct(st_file.st_mode & 0o777)]
def attempt(name, fn):
    try:
        fn()
        out[name] = "allowed"
    except PermissionError:
        out[name] = "refused"
def append():
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    os.write(fd, b'{"service":"ledger-access-test"}\n')
    os.close(fd)
attempt("append", append)
attempt("create", lambda: open(d + "/new.jsonl", "x").close())
attempt("rename", lambda: os.rename(path, d + "/moved.jsonl"))
attempt("delete", lambda: os.remove(path))
attempt("mkdir", lambda: os.mkdir(d + "/sub"))
print(json.dumps(out))
"""


def test_the_runner_can_append_to_the_ledger_and_nothing_else() -> None:
    if "running" not in docker("inspect", "-f", "{{.State.Status}}", "cs-lb", check=False):
        pytest.skip("the testbed is not running (ocp up)")
    chaos("reset")  # the helper applies the ownership on every ledger write
    before = ledger_text().splitlines()
    try:
        out = json.loads(
            docker(
                "run",
                "--rm",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "64m",
                "-v",
                f"{LEDGER_VOLUME}:/ledger",
                "--entrypoint",
                "python",
                IMAGE,
                "-c",
                PROBE,
            )  # fmt: skip
        )
        assert out["uid"] == 10001
        assert out["dir"] == [0, "0o755"] and out["file"] == [10001, "0o644"]
        assert (out["append"], out["create"], out["rename"], out["delete"], out["mkdir"]) == (
            "allowed", "refused", "refused", "refused", "refused",
        )  # fmt: skip
        after = ledger_text().splitlines()
        assert after[:-1] == before and json.loads(after[-1]) == {"service": "ledger-access-test"}
    finally:
        chaos("reset")  # removes the test line
