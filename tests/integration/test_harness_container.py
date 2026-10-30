# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

"""Integration tests for the container plumbing (test_harness/container.py).

Everything here is pure host-side mechanics — env fencing, exec command shape,
credential staging, provisioning guards — exercised without a docker daemon. The real
container round-trip is deliberately left to live harness runs, like the agent
walkthrough itself.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

from test_harness import container as container_mod
from test_harness.config import HarnessConfig
from test_harness.container import (
    DECOY_QDRANT_URL,
    HARNESS_MOUNT,
    SKILLS_MOUNT,
    build_container_env,
    exec_command,
    image_inputs_hash,
    provision_agent,
    staged_mount,
)
from test_harness.server import HarnessInfraError

THROWAWAY_URL = "http://host.docker.internal:59999"


@pytest.fixture
def staged():
    """Staging registry that cleans up its /tmp dirs (in production stop() owns this)."""
    dirs: list[Path] = []
    yield dirs
    for staging in dirs:
        shutil.rmtree(staging, ignore_errors=True)


def test_container_env_fences_both_routes():
    # Arrange / Act: the container is born with ONLY this env - the remote route
    # points at the throwaway server and any --local detour finds no Qdrant at all (mem 1701: fence both routes)
    env = build_container_env(THROWAWAY_URL)

    # Assert
    assert env["AXMEM_SERVER"] == THROWAWAY_URL
    assert env["QDRANT_URL"] == DECOY_QDRANT_URL
    assert "QDRANT_API_KEY" not in env
    assert env["RERANKING_ENABLED"] == "false"
    assert env["EMBEDDING_PROVIDER"] == "TensorRT"
    # A fresh container inherits nothing, so there must be no token to scrub - and
    # none accidentally introduced either
    assert "AXMEM_TOKEN" not in env


def test_exec_command_wraps_runner_in_os_level_timeout():
    # Arrange: host-side asyncio cancellation cannot kill a process inside the
    # container (mem 1654) - the in-container `timeout` wrapper is the real guard
    config = HarnessConfig()

    # Act
    cmd = exec_command(config.timeout_for("axmem-recall"), "axmem-recall")

    # Assert
    assert cmd[:3] == ["timeout", "--kill-after=30s", "900"]
    assert "python" in cmd
    assert "-u" in cmd
    assert f"{HARNESS_MOUNT}/runner.py" in cmd
    assert cmd[-1] == f"{SKILLS_MOUNT}/axmem-recall"


def test_staged_mount_copies_into_an_owner_only_dir(tmp_path, staged):
    # Arrange: the container runs as the invoking user, so the staging dir stays private
    # to it, and the originals (host secrets) must never be the mounted files
    secret = tmp_path / "auth.json"
    secret.write_text('{"token": "real"}')

    # Act
    volumes = staged_mount([secret], "/home/node/.local/share/opencode", staged)

    # Assert
    (host_dir,) = staged
    assert volumes == {str(host_dir): {"bind": "/home/node/.local/share/opencode", "mode": "rw"}}
    copy = host_dir / "auth.json"
    assert copy.read_text() == '{"token": "real"}'
    assert copy != secret
    if sys.platform != "win32":
        assert host_dir.stat().st_mode & 0o777 == 0o700
        assert copy.stat().st_mode & 0o777 == 0o600


def test_provision_opencode_stages_auth_and_config(tmp_path, staged):
    # Arrange
    auth = tmp_path / "auth.json"
    auth.write_text("{}")

    # Act
    volumes = provision_agent("opencode", staged, auth_path=auth)

    # Assert: credentials + the harness-owned config, both as writable staged copies
    # (agent-shell rewrites config files in place - a read-only bind would break it)
    binds = {spec["bind"] for spec in volumes.values()}
    assert binds == {"/home/node/.local/share/opencode", "/home/node/.config/opencode"}
    assert all(spec["mode"] == "rw" for spec in volumes.values())
    assert len(staged) == 2


def test_provision_opencode_missing_auth_is_infra_error(tmp_path):
    # Arrange
    missing = tmp_path / "auth.json"

    # Act / Assert
    with pytest.raises(HarnessInfraError, match="opencode auth login"):
        provision_agent("opencode", [], auth_path=missing)


def test_provision_unknown_agent_is_infra_error():
    # Act / Assert: v1 containerizes the opencode vertical only
    with pytest.raises(HarnessInfraError, match="opencode"):
        provision_agent("codex", [])


def test_harness_opencode_config_is_strict_json():
    # Arrange: agent-shell round-trips opencode.json through json.loads - JSONC
    # trailing commas break it (mem 1643), and autoupdate must be pinned off so the
    # container does not self-update mid-walkthrough
    config_path = Path("test_harness/docker/opencode.json")

    # Act
    parsed = json.loads(config_path.read_text())

    # Assert
    assert parsed["autoupdate"] is False


def test_image_inputs_hash_tracks_the_requirements():
    # Arrange / Act / Assert: same content -> same digest; any dep change -> new digest
    assert image_inputs_hash("a==1\n") == image_inputs_hash("a==1\n")
    assert image_inputs_hash("a==1\n") != image_inputs_hash("a==2\n")


@pytest.fixture
def docker_dir(tmp_path, monkeypatch):
    """A stand-in build context holding the inputs the hash reads."""
    for name in container_mod._IMAGE_INPUTS:
        (tmp_path / name).write_text(f"{name} v1\n")
    monkeypatch.setattr(container_mod, "_DOCKER_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize("name", container_mod._IMAGE_INPUTS)
def test_image_inputs_hash_tracks_every_file_the_image_build_reads(docker_dir, name):
    # Arrange
    before = image_inputs_hash("a==1\n")

    # Act: the node digest in the Dockerfile, the opencode pin and its lock are not in requirements.lock
    (docker_dir / name).write_text(f"{name} v2\n")

    # Assert
    assert image_inputs_hash("a==1\n") != before


def test_image_inputs_hash_does_not_mix_up_where_one_file_ends(docker_dir):
    # Arrange: the same bytes split differently between two files must not give the same digest
    (docker_dir / "Dockerfile").write_text("ab")
    (docker_dir / "package.json").write_text("c")
    before = image_inputs_hash("")

    # Act
    (docker_dir / "Dockerfile").write_text("a")
    (docker_dir / "package.json").write_text("bc")

    # Assert
    assert image_inputs_hash("") != before


def test_image_inputs_hash_with_a_missing_input_is_an_infra_error(docker_dir):
    # Arrange
    (docker_dir / "package-lock.json").unlink()

    # Act / Assert: a broken checkout is a harness failure (exit 2), not a traceback
    with pytest.raises(HarnessInfraError, match=r"package-lock.json"):
        image_inputs_hash("a==1\n")


def test_the_hashed_inputs_cover_every_file_the_dockerfile_copies():
    # Arrange: requirements.lock is written from the hashed `uv export`, so it is covered by the requirements
    dockerfile = (Path(container_mod._DOCKER_DIR) / "Dockerfile").read_text(encoding="utf-8")
    copied: set[str] = set()
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            *sources, _destination = (word for word in line.split()[1:] if not word.startswith("--"))
            copied.update(sources)

    # Act / Assert
    assert copied == {"requirements.lock", "package.json", "package-lock.json"}
    assert copied - {"requirements.lock"} <= set(container_mod._IMAGE_INPUTS)
    assert "Dockerfile" in container_mod._IMAGE_INPUTS
