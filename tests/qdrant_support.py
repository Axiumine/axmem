"""Real Qdrant servers for the tests: a throwaway single node, and throwaway clusters (three nodes, or as many as asked).

Nothing is simulated. The suites talk to ``qdrant/qdrant`` (the version the service requires and the gates
pin) started through Docker with an API key, on random loopback ports, and removed at the end. A run that
cannot start the container ends at once, it never skips (see ``require_docker``). ``AXMEM_TEST_QDRANT_URL``
(with ``AXMEM_TEST_QDRANT_API_KEY``) points the suites at a server that already runs, such as a
Qdrant you started yourself; the tests then keep to collections of their own and delete them afterwards.
"""
import contextlib
import os
import socket
import subprocess  # nosec B404 - runs docker with fixed argument lists, never a shell
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import pytest

IMAGE = "qdrant/qdrant:v1.19.2"
API_KEY = "axmem-test-api-key-0123456789abcdef"
READY_TIMEOUT_SECONDS = 90
PULL_TIMEOUT_SECONDS = 1800  # the image is about 150 MB, but a clean runner may sit behind a slow or rate-limited registry
LABEL = "axmem-test=1"
SCROLLS_PER_TEST = 1_000  # the page reads one integration test may make: see ``paging_loop_ends_the_test``


def _docker(*args: str, check: bool = True, timeout: float = 120) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argument list  # nosec B603 B607
        ["docker", *args],  # noqa: S607
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def ensure_image() -> None:
    """Have ``IMAGE`` locally: a clean or pruned runner, or a bumped version, pulls it instead of failing.

    An unreachable daemon or registry still fails: the pull reports the cause.
    """
    try:
        _docker("image", "inspect", IMAGE, timeout=30)
    except RuntimeError:  # not local, or the daemon does not answer (the pull then says why)
        _docker("pull", IMAGE, timeout=PULL_TIMEOUT_SECONDS)


def require_docker() -> None:
    """End the pytest run (never skip) when no Qdrant server can be started for the suites."""
    if os.environ.get("AXMEM_TEST_QDRANT_URL"):
        return
    try:
        ensure_image()
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.exit(
            f"Qdrant preflight failed: {exc}\n"
            f"These suites run against a real {IMAGE} started through Docker (pulled when it is not local, so Docker "
            "and the registry must be reachable; or set AXMEM_TEST_QDRANT_URL to a running server); nothing is skipped.",
            returncode=2,
        )


def unique_prefix() -> str:
    """A collection prefix no other test uses."""
    return f"t{uuid.uuid4().hex[:12]}"


def drop_prefix(url: str, api_key: str, prefix: str) -> None:
    """Delete every collection and alias a test run created under ``prefix``."""
    headers = {"api-key": api_key}
    with httpx.Client(base_url=url, headers=headers, timeout=30) as http:
        aliases = http.get("/aliases").json()["result"]["aliases"]
        for alias in aliases:
            if alias["alias_name"].startswith(f"{prefix}_"):
                http.delete(f"/collections/{alias['collection_name']}")
        for collection in http.get("/collections").json()["result"]["collections"]:
            if collection["name"].startswith(f"{prefix}_"):
                http.delete(f"/collections/{collection['name']}")


def wait_ready(url: str, api_key: str, timeout: float = READY_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/collections", headers={"api-key": api_key}, timeout=3).status_code == 200:
                return
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(0.3)
    raise RuntimeError(f"Qdrant at {url} was not ready after {timeout:.0f}s ({last})")


@dataclass
class QdrantServer:
    """Where the suites reach their Qdrant node."""

    url: str
    api_key: str
    container: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def start_single_node() -> QdrantServer:
    """Start (or attach to) the single node the integration and end-to-end suites share."""
    external = os.environ.get("AXMEM_TEST_QDRANT_URL")
    if external:
        key = os.environ.get("AXMEM_TEST_QDRANT_API_KEY", "")
        wait_ready(external, key)
        return QdrantServer(url=external, api_key=key)
    name = f"axmem-test-{uuid.uuid4().hex[:8]}"
    _docker(
        "run", "-d", "--rm", "--name", name, "--label", LABEL,
        # The data is thrown away with the container, so it lives in memory: payload-index creation fsyncs, and on a
        # slow Docker storage driver it costs seconds per index and per test.
        "--tmpfs", "/qdrant/storage", "--tmpfs", "/qdrant/snapshots",
        "-e", f"QDRANT__SERVICE__API_KEY={API_KEY}",
        "-p", "127.0.0.1::6333", IMAGE,
    )
    try:
        mapping = _docker("port", name, "6333/tcp").splitlines()[0]
        url = f"http://127.0.0.1:{mapping.rsplit(':', 1)[1]}"
        wait_ready(url, API_KEY)
    except BaseException:
        stop(name)
        raise
    return QdrantServer(url=url, api_key=API_KEY, container=name)


def stop(container: str | None) -> None:
    if container:
        _docker("rm", "-f", "-v", container, check=False)


# ---- clusters -------------------------------------------------------------------------------------

NODES = 3  # the size of the cluster the cluster suites share; ``start_cluster(nodes=...)`` starts another size


@dataclass
class QdrantCluster:
    """A cluster: containers, their loopback REST URLs and the network that joins them."""

    network: str
    containers: list[str]
    urls: list[str]
    api_key: str

    @property
    def nodes(self) -> int:
        return len(self.containers)

    def stop_node(self, index: int) -> None:
        _docker("stop", "-t", "1", self.containers[index])

    def kill_node(self, index: int) -> None:
        _docker("kill", self.containers[index])

    def start_node(self, index: int) -> None:
        _docker("start", self.containers[index])

    def peers_of(self, index: int) -> int:
        """How many peers node ``index`` sees, 0 while it has no leader."""
        try:
            reply = httpx.get(
                f"{self.urls[index]}/cluster", headers={"api-key": self.api_key}, timeout=3,
            ).json()["result"]
        except (httpx.HTTPError, KeyError, ValueError):
            return 0
        return len(reply["peers"]) if reply["raft_info"].get("leader") else 0

    def strong_leader(self) -> int:
        """Index of the node with the highest peer id, the one ``ordering=strong`` writes are routed through."""
        peers = httpx.get(
            f"{self.urls[0]}/cluster", headers={"api-key": self.api_key}, timeout=3,
        ).json()["result"]["peers"]
        host = urlparse(peers[max(peers, key=int)]["uri"]).hostname
        return self.containers.index(str(host))

    def a_follower(self) -> int:
        """Index of a node that is not the strong-ordering leader: one that can go without stopping writes."""
        leader = self.strong_leader()
        return next(index for index in range(self.nodes) if index != leader)

    def wait_formed(self, timeout: float = READY_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(self.peers_of(i) == self.nodes for i in range(self.nodes)):
                return
            time.sleep(0.5)
        raise RuntimeError("the Qdrant cluster did not form")


def free_loopback_port() -> int:
    """A port nothing listens on now; a node that is stopped and started again must keep the one it was given."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_cluster(nodes: int = NODES, *, in_memory: bool = False) -> QdrantCluster:
    """Start ``nodes`` nodes that form one cluster with internal authentication, ``QDRANT__SERVICE__API_KEY`` set.

    The containers are not started with ``--rm`` (a stopped node has to be started again with its data) and their
    REST ports are fixed; ``stop_cluster`` removes them, and the ``axmem-test`` label finds any a killed run left.
    ``in_memory`` keeps the data of every node in memory (``--tmpfs``), as the single node does: payload-index creation
    fsyncs, and on a busy disk that costs minutes per test. Such a cluster cannot stop a node and start it again,
    because the data goes with the container's stop; the suites that only restore into it or read it use this.
    """
    tag = uuid.uuid4().hex[:6]
    network = f"axmem-net-{tag}"
    _docker("network", "create", network)
    containers = [f"axmem-q{tag}-{i + 1}" for i in range(nodes)]
    urls: list[str] = []
    try:
        for index, name in enumerate(containers):
            command = ["./qdrant", "--uri", f"http://{name}:6335"]
            if index:
                command = ["./qdrant", "--bootstrap", f"http://{containers[0]}:6335", "--uri", f"http://{name}:6335"]
            port = free_loopback_port()
            urls.append(f"http://127.0.0.1:{port}")
            memory = ["--tmpfs", "/qdrant/storage", "--tmpfs", "/qdrant/snapshots"] if in_memory else []
            _docker(
                "run", "-d", "--name", name, "--label", LABEL, "--network", network, *memory,
                "-e", "QDRANT__CLUSTER__ENABLED=true",
                "-e", f"QDRANT__SERVICE__API_KEY={API_KEY}",
                "-e", "QDRANT__SERVICE__ENFORCE_INTERNAL_AUTH=true",
                "-p", f"127.0.0.1:{port}:6333", IMAGE, *command,
            )
            time.sleep(3 if index == 0 else 1)
        cluster = QdrantCluster(network=network, containers=containers, urls=urls, api_key=API_KEY)
        cluster.wait_formed()
    except BaseException:
        stop_cluster(QdrantCluster(network, containers, urls, API_KEY))
        raise
    return cluster


def stop_cluster(cluster: QdrantCluster) -> None:
    for name in cluster.containers:
        _docker("rm", "-f", "-v", name, check=False)
    with contextlib.suppress(RuntimeError):
        _docker("network", "rm", cluster.network, check=False)
