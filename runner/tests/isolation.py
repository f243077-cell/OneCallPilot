"""The runner's isolation policy (RUN-016, architecture §7.3, CLAUDE.md §4.8 and §6.5),
checked two ways:

- `service_problems`: a compose service definition (the intended spec in
  tests/runner_service.yml, and the `runner` service of runner.yml once it is
  there); used by tests/test_isolation.py;
- `container_problems`: a running container's `docker inspect`, with its image's;
  used by the live test against the runner the live stack started.

Each returns the list of problems; an empty list means the policy holds.
"""

from typing import Any

# §7.3, plus DOCKER_API_VERSION (CLAUDE.md §10.2, RUN-017: the proxy blocks /version).
ALLOWED_ENV = frozenset({
    "RUNNER_REDIS_URL", "ACTION_SIGNING_KEY", "RUNNER_LINK_KEY", "LEDGER_PATH",
    "DOCKER_HOST", "DOCKER_API_VERSION", "RUNNER_ADMIN_TOKEN",
})  # fmt: skip
FORBIDDEN_ENV = (
    "DATABASE_URL", "INGEST_TOKEN", "FCM_CREDENTIALS_PATH", "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
)  # fmt: skip
DOCKER_HOST = "tcp://socket-proxy-rw:2375"
NETWORKS = frozenset({"rw_proxy_net", "copilot_net", "chaos_net"})
MEMORY = "128m"
MEMORY_BYTES = 128 * 1024 * 1024
LEDGER_MOUNT = "deploy_ledger:/ledger"
NEVER_IN_SERVICE = ("privileged", "cap_add", "pid", "ipc", "network_mode", "userns_mode",
                    "devices", "ports", "env_file", "volumes_from", "cgroup_parent")  # fmt: skip


def _root(user: str) -> bool:
    return user.split(":")[0] in ("", "0", "root")


def _env(value: Any) -> dict[str, str]:
    """Compose allows a mapping or a list of KEY=VALUE."""
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return dict(str(item).partition("=")[::2] for item in value or [])


def _common(user: str, env: dict[str, str]) -> list[str]:
    problems = []
    if _root(user):
        problems.append(f"runs as root (user {user!r})")
    if set(env) != ALLOWED_ENV:
        extra, missing = sorted(set(env) - ALLOWED_ENV), sorted(ALLOWED_ENV - set(env))
        problems.append(f"environment: extra {extra}, missing {missing}")
    problems += [f"receives {name}" for name in FORBIDDEN_ENV if name in env]
    if env.get("DOCKER_HOST") != DOCKER_HOST:
        problems.append(f"DOCKER_HOST is {env.get('DOCKER_HOST')!r}, not {DOCKER_HOST}")
    return problems


def service_problems(service: dict[str, Any]) -> list[str]:
    problems = _common(str(service.get("user", "")), _env(service.get("environment")))
    if service.get("read_only") is not True:
        problems.append("root filesystem is not read-only")
    if service.get("tmpfs") != ["/tmp"]:
        problems.append(f"tmpfs is {service.get('tmpfs')!r}, not ['/tmp']")
    if service.get("cap_drop") != ["ALL"]:
        problems.append("does not drop ALL capabilities")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        problems.append("no-new-privileges is not set")
    if service.get("mem_limit") != MEMORY:
        problems.append(f"memory limit is {service.get('mem_limit')!r}, not {MEMORY}")
    if set(service.get("networks", [])) != NETWORKS:
        problems.append(f"networks are {service.get('networks')!r}")
    volumes = [str(v) for v in service.get("volumes", [])]
    if any("docker.sock" in v for v in volumes):
        problems.append("mounts the Docker socket")
    if volumes != [LEDGER_MOUNT]:
        problems.append(f"mounts {volumes!r}, not only {LEDGER_MOUNT}")
    problems += [f"sets {key}" for key in NEVER_IN_SERVICE if service.get(key)]
    return problems


def container_problems(
    container: dict[str, Any], image: dict[str, Any], networks: set[str]
) -> list[str]:
    """`docker inspect` of the running runner and of its image. Only the
    variables given at run time count as its environment (the base image sets
    PATH, LANG and the like)."""
    host, config = container["HostConfig"], container["Config"]
    baked = set(_env(image["Config"].get("Env")))
    env = {k: v for k, v in _env(config.get("Env")).items() if k not in baked}
    problems = _common(config.get("User") or image["Config"].get("User") or "", env)
    if host.get("ReadonlyRootfs") is not True:
        problems.append("root filesystem is not read-only")
    if set(host.get("Tmpfs") or {}) != {"/tmp"}:
        problems.append(f"tmpfs is {host.get('Tmpfs')!r}")
    if host.get("CapDrop") != ["ALL"]:
        problems.append(f"CapDrop is {host.get('CapDrop')!r}")
    if host.get("CapAdd"):
        problems.append(f"CapAdd is {host.get('CapAdd')!r}")
    if not {"no-new-privileges", "no-new-privileges:true"} & set(host.get("SecurityOpt") or []):
        problems.append("no-new-privileges is not set")
    if host.get("Privileged"):
        problems.append("privileged")
    if host.get("Memory") != MEMORY_BYTES:
        problems.append(f"memory limit is {host.get('Memory')} bytes")
    if host.get("PortBindings"):
        problems.append("publishes ports")
    mounts = container.get("Mounts") or []
    if any("docker.sock" in str(m.get("Source", "")) + str(m.get("Destination", ""))
           for m in mounts):  # fmt: skip
        problems.append("mounts the Docker socket")
    if [m.get("Destination") for m in mounts] != ["/ledger"]:
        problems.append(f"mounts {[m.get('Destination') for m in mounts]!r}, not only /ledger")
    if set(container["NetworkSettings"]["Networks"]) != networks:
        problems.append(f"networks are {sorted(container['NetworkSettings']['Networks'])}")
    return problems
