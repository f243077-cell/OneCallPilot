"""ocp doctor: check versions, health, published ports and the socket rule.

Fails (exit 1) on any port the project publishes against architecture §3
(SEC-013), any container other than the socket proxies that mounts the Docker
socket, an unpinned image, or a service that is not running and healthy.
Ports of containers outside the project that are open to the LAN are reported
as warnings: on the integration host they would be reachable from the phone's
network too.
"""

import argparse

from ocp_cli import policy
from ocp_cli.stack import COMPOSE_MIN, Stack, StackError

NAME = "doctor"
HELP = "check versions, health, published ports and the Docker socket rule"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def run(args: argparse.Namespace, stack: Stack) -> int:
    failures = 0

    def report(title: str, problems: list[str], *, warn: bool = False) -> None:
        nonlocal failures
        if not problems:
            print(f"OK    {title}")
            return
        print(f"{'WARN' if warn else 'FAIL'}  {title}")
        for problem in problems:
            print(f"        - {problem}")
        if not warn:
            failures += 1

    server = stack.check(["docker", "version", "--format", "{{.Server.Version}}"]).stdout.strip()
    version = stack.compose_version()
    compose_ok = version >= COMPOSE_MIN
    report(
        f"Docker {server}, Compose {'.'.join(map(str, version))} (needs >= 2.20)",
        [] if compose_ok else ["Compose is older than 2.20"],
    )
    memory = stack.check(["docker", "info", "--format", "{{.MemTotal}}"]).stdout.strip()
    if memory.isdigit():
        print(f"INFO  Docker memory {int(memory) / (1 << 30):.1f} GiB")

    try:
        expected = stack.expected_services()
        images = stack.images()
    except StackError as exc:
        report("compose configuration (is .env present?)", [str(exc)])
        return 1
    containers = stack.containers()
    report(
        f"{len(expected)} services running and healthy",
        policy.health_problems(expected, containers),
    )
    published = sorted(
        f"{c.service} {host_ip or '0.0.0.0'}:{host_port}"
        for c in containers
        for _, host_ip, host_port in c.port_bindings
    )
    report(
        "published ports match the architecture port table: " + (", ".join(published) or "none"),
        policy.port_problems(containers),
    )
    report("only the socket proxies mount the Docker socket", policy.socket_problems(containers))
    report(f"{len(images)} images use pinned tags", policy.image_problems(images))

    project = {c.name for c in containers}
    others = [
        c
        for c in stack.containers(project_only=False)
        if c.name not in project and c.state == "running"  # a stopped one exposes nothing
    ]
    report(
        "no other container on this host is open to the LAN", policy.lan_exposed(others), warn=True
    )

    print("doctor: " + ("all checks passed" if failures == 0 else f"{failures} check(s) failed"))
    return 0 if failures == 0 else 1
