#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

#
# smoke-release.sh — install the files of a release from a package index, start the server from them and use it.
#
# Usage:
#   ./scripts/smoke-release.sh [--keep] [--index testpypi|pypi] <version>      (default index: testpypi)
#
# The install check is advisory, not a gate: no hook, no workflow and no other script runs it or reads its result, and it runs on
# the machine that has the NVIDIA GPU, Docker and the models of Axmem (the one of the pre-push gates) only. Run it with
# `--index testpypi` after scripts/release-testpypi.sh has printed "TestPyPI rehearsal complete", and with `--index pypi` after
# scripts/release-pypi.sh has printed "Release complete". It uploads nothing, writes nothing in the repository, dispatches
# nothing and needs no credential of yours: the only secrets are a random Qdrant key and a random bearer token, made here and
# gone with the run.
#
# It proves that what the index serves for the version is, byte for byte, what the signed tag records (the sha256 of
# release-digests.json), that the wheel installs by its path into a new Python 3.14 environment with the dependencies that
# pypi.org and pypi.nvidia.com serve today, that the installed command runs, and that the installed server starts on this
# machine's GPU and models, behind bearer-token authentication, against a Qdrant of its own, and keeps a memory it was given and
# finds it by a paraphrase (the embedder and the reranker answer). It does not prove the signatures and the attestations (docs/release.md,
# "Checking a release"), the sdist (its bytes are compared, it is not installed), the trusted publisher or the environment of the
# index (the rehearsal and the upload do), another Python, platform or GPU, the locked dependencies of uv.lock (the install resolves
# afresh), or a machine whose own configuration, Qdrant and models are not the ones of this one. A pass says nothing about the next
# release, and nothing about a later state of the index (a yank, a new version).
#
# Steps, in the order they run. Refused before anything is read or started: a number of arguments other than one, an unknown
# option, an index that is not testpypi or pypi, a version that is not canonical (the grammar of release-pypi.sh), a tool that
# is missing (git jq uv curl sha256sum timeout docker nvidia-smi openssl), no NVIDIA GPU that answers, no Docker daemon that answers.
#   1. the digests of the tag's release-digests.json are the truth (the tag is signed; the index is not trusted)
#   2. the JSON of the index lists exactly the files of the manifest, on the files host of the index, with its digests
#   3. the files are downloaded from that host over https only and their sha256 is the manifest's
#   4. the wheel is installed by its path into a new venv; TestPyPI is never an index of that install (a name on a scratch
#      index that is not Axmem's would be taken for a dependency), and pypi.nvidia.com is the only extra one (README,
#      Installing the package); the install is consistent (uv pip check), reports the version, and `axmem --help` runs
#   5. a Qdrant container is started for this run only (loopback port, random API key, memory-backed storage)
#   6. `axmem serve` of the new venv is started on a loopback port with static bearer-token authentication, against that Qdrant;
#      the run waits for /health, which answers once the TensorRT embedder and reranker are loaded
#   7. the installed CLI, as a client of that server with the token: lists the tools, is refused with a wrong token, saves a
#      memory and three others, finds the first by a paraphrase through the embedder and the reranker, reads it back, and
#      is told an unknown id does not exist
#   8. the server is stopped (SIGTERM), then the container is removed; neither is left behind, whatever happens
#
# The venv, the logs and the files live in a temporary directory that is removed at the end; --keep leaves it and prints it.
# The server and the client run with a clean environment (no .env, no AXMEM_* or QDRANT_* of the shell, no proxy for loopback),
# so that the machine's own configuration and Qdrant are never read or written.
#
# The manifest is read from the tag in the checkout that holds this script (git fetch the tags first if the tag is not here).
# The last line printed on success is "<index> install and server check passed: axmem <version>". See docs/release.md.
#
set -euo pipefail

# The constants of the check, in one place. The hosts are the shell's copy of the two rows of the table in scripts/release/facts.py
# (and of scripts/lib/project.sh and scripts/lib/testpypi-flow.sh), the image is the one of tests/qdrant_support.py, and the extra
# index is the one of pyproject.toml; tests/integration/test_release_smoke_script.py holds them together.
TESTPYPI_HOST="test.pypi.org"                       # the JSON API of the project on TestPyPI: https://<host>/pypi/<PKG>/<version>/json
TESTPYPI_FILES_HOST="test-files.pythonhosted.org"   # the only host the files of TestPyPI are downloaded from
PYPI_HOST="pypi.org"                                # the same on PyPI
PYPI_FILES_HOST="files.pythonhosted.org"            # the only host the files of PyPI are downloaded from
NVIDIA_INDEX="https://pypi.nvidia.com"              # the one extra index of the install (the three TensorRT packages)
QDRANT_IMAGE="qdrant/qdrant:v1.19.2"                # the Qdrant of the test suites
CONTAINER_LABEL="axmem-wheelsmoke=1"                # on the container of this run, to find one that a killed run left
PKG="axmem"
HEALTH_TIMEOUT_S=300
INDEX="testpypi"
CURL=(curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 --max-time 600 --retry 3)

USAGE="Usage: $(basename "$0") [--keep] [--index testpypi|pypi] <version>"
err() { echo "ERROR: $*" >&2; exit 1; }

KEEP=0
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --keep) KEEP=1 ;;
    --index) (( $# >= 2 )) || err "--index needs a value, testpypi or pypi. ${USAGE}"
             shift; INDEX="$1" ;;
    *) err "unknown option $1. ${USAGE}" ;;
  esac
  shift
done
(( $# == 1 )) || err "exactly one argument, the version. ${USAGE}"
case "$INDEX" in
  testpypi) INDEX_HOST="$TESTPYPI_HOST"; INDEX_FILES_HOST="$TESTPYPI_FILES_HOST" ;;
  pypi) INDEX_HOST="$PYPI_HOST"; INDEX_FILES_HOST="$PYPI_FILES_HOST" ;;
  *) err "--index is testpypi or pypi, not '${INDEX}'" ;;
esac
VERSION="$1"
# the grammar of rf_require_version (scripts/lib/release-flow.sh): every version that release-pypi.sh releases, and no other
part='(0|[1-9][0-9]*)'
[[ "$VERSION" =~ ^${part}\.${part}\.${part}((a|b|rc)${part})?(\.post${part})?(\.dev${part})?$ ]] \
  || err "'${VERSION}' is not a canonical version: X.Y.Z with optional aN, bN, rcN, .postN, .devN (for example 0.6.0rc1)"
TAG="v${VERSION}"

for tool in git jq uv curl sha256sum timeout docker nvidia-smi openssl; do
  command -v "$tool" >/dev/null 2>&1 || err "'${tool}' is not installed"
done
nvidia-smi -L >/dev/null 2>&1 || err "no NVIDIA GPU answers (nvidia-smi -L): the server of Axmem needs one"
docker info >/dev/null 2>&1 || err "the Docker daemon does not answer: the run starts a Qdrant container"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)" \
  || err "${SCRIPT_DIR} is not in a git checkout: the manifest is read from the tag"

WORK="$(mktemp -d)"
CONTAINER=""
SERVER_PID=""

# Stops what this run started, server first (it holds the Qdrant client), then the container, then the files. Runs on every
# exit, on an error, and on INT and TERM; it never fails the exit status.
cleanup() {
  local status=$?
  set +e
  trap - EXIT INT TERM
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null
    for _ in $(seq 1 100); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 0.1; done
    kill -0 "$SERVER_PID" 2>/dev/null && kill -KILL "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
  fi
  [[ -n "$CONTAINER" ]] && docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  if (( KEEP )); then echo "kept: ${WORK}"; else rm -rf "$WORK"; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 1. The manifest of the tag: one "name sha256" line per file.
git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/tags/${TAG}^{commit}" >/dev/null \
  || err "no tag ${TAG} here (git fetch the tags of the remote that holds it)"
git -C "$REPO_ROOT" show "${TAG}:release-digests.json" > "${WORK}/manifest.json" \
  || err "the tag ${TAG} holds no release-digests.json"
[[ "$(jq -r '.version' "${WORK}/manifest.json")" == "$VERSION" ]] || err "the manifest of ${TAG} is for another version"
[[ "$(jq -r '.package' "${WORK}/manifest.json")" == "$PKG" ]] || err "the manifest of ${TAG} is for another package"
jq -r '.artifacts[] | "\(.name) \(.sha256)"' "${WORK}/manifest.json" > "${WORK}/expected.txt"
n_expected="$(wc -l < "${WORK}/expected.txt")"
(( n_expected >= 1 )) || err "the manifest of ${TAG} lists no file"
echo "manifest of ${TAG}: ${n_expected} file(s)"

# 2. What the index lists for the version.
"${CURL[@]}" "https://${INDEX_HOST}/pypi/${PKG}/${VERSION}/json" > "${WORK}/index.json" \
  || err "${INDEX_HOST} does not serve ${PKG} ${VERSION} (is it uploaded?)"
[[ "$(jq -r '.info.version' "${WORK}/index.json")" == "$VERSION" ]] || err "${INDEX_HOST} answered for another version"
listed="$(jq -r '.urls[] | .filename' "${WORK}/index.json" | sort)"
wanted="$(cut -d' ' -f1 "${WORK}/expected.txt" | sort)"
[[ "$listed" == "$wanted" ]] || err "${INDEX_HOST} lists other files than the manifest. listed: $(echo "$listed" | tr '\n' ' ') wanted: $(echo "$wanted" | tr '\n' ' ')"

# 3. Download each file from the files host only, and compare the bytes with the manifest.
mkdir "${WORK}/files"
while read -r name sha; do
  url="$(jq -r --arg n "$name" '.urls[] | select(.filename == $n) | .url' "${WORK}/index.json")"
  [[ "$url" == "https://${INDEX_FILES_HOST}/"* ]] || err "${name}: its URL is not on ${INDEX_FILES_HOST}: ${url}"
  [[ "$(jq -r --arg n "$name" '.urls[] | select(.filename == $n) | .digests.sha256' "${WORK}/index.json")" == "$sha" ]] \
    || err "${name}: ${INDEX_HOST} lists another sha256 than the manifest"
  "${CURL[@]}" -o "${WORK}/files/${name}" "$url" || err "${name}: the download failed"
  got="$(sha256sum "${WORK}/files/${name}" | cut -d' ' -f1)"
  [[ "$got" == "$sha" ]] || err "${name}: the downloaded bytes have sha256 ${got}, the manifest says ${sha}"
  echo "ok  ${sha}  ${name}"
done < "${WORK}/expected.txt"

# 4. Install the wheel by its path into a new venv. No uv or pip configuration is read, and the install runs outside the
# repository, so pyproject.toml's indexes do not apply; only the variables that name an index are cleared.
WHEEL="${WORK}/files/${PKG}-${VERSION}-py3-none-any.whl"
[[ -f "$WHEEL" ]] || err "the manifest holds no wheel named $(basename "$WHEEL")"
unset UV_INDEX UV_INDEX_URL UV_EXTRA_INDEX_URL UV_DEFAULT_INDEX PIP_INDEX_URL PIP_EXTRA_INDEX_URL
cd "$WORK"
VENV="${WORK}/venv"
uv venv --no-config --quiet --python 3.14 "$VENV"
echo "installing ${PKG} ${VERSION} (the dependencies come from ${PYPI_HOST} and ${NVIDIA_INDEX}; the TensorRT files are large)"
uv pip install --no-config --quiet --python "${VENV}/bin/python" --extra-index-url "$NVIDIA_INDEX" "$WHEEL"
uv pip check --python "${VENV}/bin/python" || err "uv pip check found a broken dependency"
installed="$("${VENV}/bin/python" -I -c "import importlib.metadata as m; print(m.version('${PKG}'))")"
[[ "$installed" == "$VERSION" ]] || err "the venv reports ${PKG} ${installed}, expected ${VERSION}"
timeout 180 "${VENV}/bin/${PKG}" --help > "${WORK}/help.txt" 2>&1 \
  || { tail -20 "${WORK}/help.txt" >&2; err "'${PKG} --help' failed"; }
echo "'${PKG} --help' ran: $(wc -l < "${WORK}/help.txt") line(s)"

# 5. A Qdrant for this run: loopback only, a port Docker picks, a random key, storage in memory, a label of its own. The
# trap is set above, before the container exists.
docker image inspect "$QDRANT_IMAGE" >/dev/null 2>&1 || docker pull --quiet "$QDRANT_IMAGE" >/dev/null \
  || err "the image ${QDRANT_IMAGE} is not here and cannot be pulled"
QDRANT_KEY="$(openssl rand -hex 16)"
CONTAINER="axmem-wheelsmoke-$$-${RANDOM}"
QDRANT__SERVICE__API_KEY="$QDRANT_KEY" docker run -d --rm --name "$CONTAINER" --label "$CONTAINER_LABEL" \
  --tmpfs /qdrant/storage --tmpfs /qdrant/snapshots -e QDRANT__SERVICE__API_KEY -p 127.0.0.1::6333 "$QDRANT_IMAGE" >/dev/null \
  || err "the Qdrant container did not start"
QDRANT_PORT="$(docker port "$CONTAINER" 6333/tcp | head -1 | sed 's/.*://')"
[[ "$QDRANT_PORT" =~ ^[0-9]+$ ]] || err "no host port for the Qdrant container"
QDRANT_URL="http://127.0.0.1:${QDRANT_PORT}"
for _ in $(seq 1 300); do
  curl --noproxy '*' -fsS -o /dev/null -H "api-key: ${QDRANT_KEY}" "${QDRANT_URL}/collections" 2>/dev/null && break
  [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == "true" ]] || err "the Qdrant container stopped"
  sleep 0.3
done
curl --noproxy '*' -fsS -o /dev/null -H "api-key: ${QDRANT_KEY}" "${QDRANT_URL}/collections" || err "Qdrant did not answer in 90 s"
echo "Qdrant ${QDRANT_IMAGE} answers at ${QDRANT_URL} (container ${CONTAINER})"

# 6. The server of the installed wheel. A clean environment: only what a start needs and the machine's own places for the
# models and the tokenizer (HOME, XDG_DATA_HOME, TMPDIR), so that the models of this machine are found and nothing else of the
# shell (a QDRANT_URL, an AXMEM_SERVER, a proxy) reaches it. XDG_CONFIG_HOME is an empty directory: no ~/.config/axmem/.env.
mkdir "${WORK}/xdg"
SAFE_ENV=(PATH="$PATH" HOME="$HOME" XDG_CONFIG_HOME="${WORK}/xdg" NO_PROXY="127.0.0.1,localhost,::1" no_proxy="127.0.0.1,localhost,::1"
  NO_COLOR=1 PYTHONUNBUFFERED=1)
for v in XDG_DATA_HOME TMPDIR CUDA_VISIBLE_DEVICES LD_LIBRARY_PATH HF_HOME HF_HUB_OFFLINE; do
  if [[ -n "${!v:-}" ]]; then SAFE_ENV+=("$v=${!v}"); fi
done
TOKEN="$(openssl rand -hex 24)"
TOKEN_SHA="$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)"
SERVER_PORT="$("${VENV}/bin/python" -I -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")"
SERVER_URL="http://127.0.0.1:${SERVER_PORT}"
env -i "${SAFE_ENV[@]}" QDRANT_URL="$QDRANT_URL" QDRANT_API_KEY="$QDRANT_KEY" QDRANT_COLLECTION_PREFIX="axsmoke$(openssl rand -hex 4)" \
  AUTH_MODE=static AUTH_STATIC_TOKEN_HASHES="{\"smoke\":\"${TOKEN_SHA}\"}" \
  "${VENV}/bin/${PKG}" serve --transport http --host 127.0.0.1 --port "$SERVER_PORT" > "${WORK}/server.log" 2>&1 &
SERVER_PID=$!
echo "waiting for ${SERVER_URL}/health (the engines load first; at most ${HEALTH_TIMEOUT_S} s)"
deadline=$(( SECONDS + HEALTH_TIMEOUT_S ))
until curl --noproxy '*' -fsS -o /dev/null "${SERVER_URL}/health" 2>/dev/null; do
  kill -0 "$SERVER_PID" 2>/dev/null || { tail -30 "${WORK}/server.log" >&2; err "the server exited before it was healthy"; }
  (( SECONDS < deadline )) || { tail -30 "${WORK}/server.log" >&2; err "the server was not healthy in ${HEALTH_TIMEOUT_S} s"; }
  sleep 1
done
echo "ok  server healthy: $(curl --noproxy '*' -fsS "${SERVER_URL}/health")"

# 7. The installed CLI as a client of that server. --server and --json go after the verb. Each call runs with a clean
# environment too, and the token only in the environment of the calls that carry it.
cli() { # <token> <args...>: prints stdout; stderr to cli.err
  local token="$1"; shift
  timeout 330 env -i "${SAFE_ENV[@]}" AXMEM_TOKEN="$token" "${VENV}/bin/${PKG}" "$@" --server "$SERVER_URL" 2> "${WORK}/cli.err"
}
must() { # <what> <token> <args...>: the call must exit 0; the output is left in $OUT
  local what="$1"; shift
  OUT="$(cli "$@")" || { cat "${WORK}/cli.err" >&2; err "${what}: the call failed"; }
}
assert() { # <what> <jq filter>: $OUT must satisfy it
  jq -e "$2" <<< "$OUT" >/dev/null || { echo "$OUT" | head -c 1500 >&2; echo >&2; err "$1: the answer is not as expected"; }
  echo "ok  $1"
}

must "tools list" "$TOKEN" tools list --json
assert "the server lists its tools (memory among them)" '.total_count > 0 and (.tools_by_category | has("memory"))'

if cli "wrong-token-$(openssl rand -hex 8)" tools list --json > /dev/null; then err "a wrong token was accepted"; fi
echo "ok  a wrong token is refused"

MARKER="smk-$(date +%s)-${RANDOM}"
must "save the target" "$TOKEN" memory save "The cat sat on the warm windowsill every morning. ${MARKER}" --title "Smoke target" --importance 7 --json
assert "the target is saved" '.id | type == "string"'
TARGET_ID="$(jq -r '.id' <<< "$OUT")"
must "save a distractor" "$TOKEN" memory save "Quarterly tax filing deadlines for small companies" --title "Taxes" --importance 7 --json
must "save a distractor" "$TOKEN" memory save "How to bake sourdough bread with a long cold rise" --title "Bread" --importance 7 --json
must "save a distractor" "$TOKEN" memory save "Rust borrow checker lifetimes and what they prove" --title "Rust" --importance 7 --json

must "search by a paraphrase" "$TOKEN" memory search "a feline resting by the sunny window" -c "smoke test" -n 1 --json
jq -e --arg id "$TARGET_ID" --arg m "$MARKER" \
  '.primary_memories[0] | (.id == $id and (.content | contains($m)))' <<< "$OUT" >/dev/null \
  || { echo "$OUT" | head -c 1500 >&2; echo >&2; err "the paraphrase did not find the saved memory first"; }
jq -e '.scores[0] | (.rerank_score != null and .similarity > 0)' <<< "$OUT" >/dev/null \
  || { echo "$OUT" | head -c 1500 >&2; echo >&2; err "the reranker did not score the first hit"; }
echo "ok  the paraphrase finds the saved memory first, with a similarity and a rerank score"

must "read the target back" "$TOKEN" memory get "$TARGET_ID" --json
jq -e --arg m "$MARKER" '.content | contains($m)' <<< "$OUT" >/dev/null || err "the memory read back is not the one saved"
echo "ok  the saved memory reads back"

if cli "$TOKEN" memory get 00000000-0000-0000-0000-000000000000 --json > /dev/null; then err "an unknown id was found"; fi
echo "ok  an unknown id is reported as an error"

# 8. Stop the server first and check it is gone, then the container; the trap does the same if anything above failed.
kill -TERM "$SERVER_PID"
for _ in $(seq 1 100); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 0.1; done
if kill -0 "$SERVER_PID" 2>/dev/null; then err "the server did not stop on SIGTERM in 10 s"; fi
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""
docker rm -f -v "$CONTAINER" >/dev/null 2>&1 || true
[[ -z "$(docker ps -aq --filter "name=^${CONTAINER}\$")" ]] || err "the Qdrant container is still there"
CONTAINER=""
echo "ok  server and Qdrant container stopped and removed"

echo "${INDEX} install and server check passed: ${PKG} ${VERSION}"
