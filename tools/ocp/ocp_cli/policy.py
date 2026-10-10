"""The stack's exposure and safety rules as data (architecture §3, CLAUDE.md §4.8, §6).

Pure functions over `Container`s, so `ocp doctor` and its tests share them.
"""

from dataclasses import dataclass

from ocp_cli.stack import Container

LOOPBACK = frozenset({"127.0.0.1"})
ANY = frozenset({"", "0.0.0.0", "::"})


@dataclass(frozen=True)
class Exposure:
    host_port: str
    host_ips: frozenset[str]


# architecture §3, "Published ports on the host". Nothing else is published.
PUBLISHED: dict[str, Exposure] = {
    "backend-api": Exposure("8000", ANY),  # the phone on the LAN needs it
    "grafana": Exposure("3000", LOOPBACK),
    "prometheus": Exposure("9090", LOOPBACK),
    "loki": Exposure("3100", LOOPBACK),
    "cs-lb": Exposure("8080", LOOPBACK),
}

# CLAUDE.md §4.8: only these two containers ever mount the Docker socket.
SOCKET_SERVICES = frozenset({"socket-proxy-ro", "socket-proxy-rw"})


def port_problems(containers: list[Container]) -> list[str]:
    """Every published port of the project that §3 does not allow."""
    problems: list[str] = []
    for c in containers:
        allowed = PUBLISHED.get(c.service)
        for container_port, host_ip, host_port in c.port_bindings:
            where = f"{host_ip or '0.0.0.0'}:{host_port}->{container_port}"
            if allowed is None:
                problems.append(f"{c.name} publishes {where}; {c.service} may publish nothing")
            elif host_port != allowed.host_port or host_ip not in allowed.host_ips:
                rule = (
                    "any interface"
                    if allowed.host_ips == ANY
                    else "/".join(sorted(allowed.host_ips))
                )
                problems.append(
                    f"{c.name} publishes {where}; allowed is {rule} port {allowed.host_port}"
                )
    return problems


def lan_exposed(containers: list[Container]) -> list[str]:
    """Ports of any container bound to every interface, so reachable from the LAN."""
    return [
        f"{c.name} {host_port}->{container_port}"
        for c in containers
        for container_port, host_ip, host_port in c.port_bindings
        if host_ip in ANY
    ]


def socket_problems(containers: list[Container]) -> list[str]:
    return [
        f"{c.name} mounts the Docker socket; only {', '.join(sorted(SOCKET_SERVICES))} may"
        for c in containers
        if c.service not in SOCKET_SERVICES and any("docker.sock" in m for m in c.mounts)
    ]


def image_problems(images: dict[str, str]) -> list[str]:
    """CLAUDE.md §4.8: pinned tags only, never `latest`."""
    problems = []
    for service, image in sorted(images.items()):
        if "@sha256:" in image:
            continue
        name, _, tag = image.rpartition(":")
        if not name or "/" in tag or tag in ("", "latest"):
            problems.append(f"{service} uses {image or 'no image'}; pin a tag")
    return problems


def health_problems(expected: list[str], containers: list[Container]) -> list[str]:
    """Every service the profiles start must be running and healthy; every service
    has a health check (architecture §12.1)."""
    by_service = {c.service: c for c in containers}
    problems = []
    for service in expected:
        c = by_service.get(service)
        if c is None:
            problems.append(f"{service} has no container")
        elif c.state != "running":
            problems.append(f"{service} is {c.state}")
        elif c.health == "":
            problems.append(f"{service} has no health check")
        elif c.health != "healthy":
            problems.append(f"{service} is {c.health}")
    return problems
