# SPDX-FileCopyrightText: 2025 Scott Raisbeck
# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only AND MIT

# The UAT walkthrough container: opencode + agent-shell + the locked axmem deps.
# The working-tree wheel installs --no-deps at container start (see container.py), so
# a code change never needs an image rebuild - only a dependency change does, and
# ensure_image() detects that automatically: its hash label covers requirements.lock,
# this Dockerfile, package.json and package-lock.json.
# node:24, multi-arch index digest resolved on 2026-10-29. To move to a newer base:
# `docker buildx imagetools inspect node:24`, copy the top-level Digest here.
FROM node:24@sha256:e7add2a490bb17d97b485b8641bd88455fff7839ade0f5eba493c3508124d413

# node:24 inherits docker-entrypoint.sh which prepends `node` to any command it can't
# resolve, mangling python/opencode invocations. Clear it so commands run directly.
ENTRYPOINT []

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        procps \
        ca-certificates \
        curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# uv manages Python 3.14 since Debian bookworm only ships 3.11 (axmem needs 3.14+).
# Download-then-run (not curl|sh) so a failed download fails the build.
RUN curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh && \
    env UV_INSTALL_DIR=/usr/local/bin sh /tmp/uv-install.sh && \
    rm /tmp/uv-install.sh

# Install under /opt so the non-root `node` user can read it. The harness runs this
# container as the invoking host user (see container.py) so the bind mounts keep their
# private host modes; that uid is arbitrary, so the venv the working-tree wheel installs
# into at container start is made writable for any uid (inside a throwaway sandbox this
# widens nothing). The chmod is in the same RUN that creates the files: a separate
# trailing `chmod -R` layer would copy every file up and add ~0.9 GB to the image.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
COPY requirements.lock /tmp/requirements.lock
RUN uv python install 3.14 && \
    uv venv /opt/venv --python 3.14 && \
    uv pip install --python /opt/venv/bin/python -r /tmp/requirements.lock && \
    uv pip install --python /opt/venv/bin/python "agent-shell-py>=0.1.16" && \
    chown -R node:node /opt/venv && \
    chmod -R a+rwX /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# npm global prefix relocated so the non-root `node` user can install globals; /opt/agents
# holds the agent CLIs that package-lock.json pins (see the npm ci step below).
RUN mkdir -p /usr/local/share/npm-global/bin /opt/agents && \
    chown -R node:node /usr/local/share /opt/agents

RUN mkdir -p /skills /home/node/.local/share/opencode /home/node/.config/opencode && \
    chown -R node:node /skills /home/node/.local /home/node/.config

WORKDIR /skills

USER node

ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH=$PATH:/usr/local/share/npm-global/bin

# HOME (uv cache, agent state) must be writable for an arbitrary uid too. All of
# /home/node is opened, not just its .local and dot-config subtrees: npm and node create
# /home/node/.cache only while this RUN executes, so the chmod has to run in the same
# RUN, and a narrower one would leave ~/.cache unwritable and break `uv pip install`.
#
# opencode is installed from the committed lock file (`npm ci` fails if package.json and
# package-lock.json disagree), not `npm install -g opencode-ai@latest`, so a rebuild
# yields the same opencode. npm ci keeps install scripts on: opencode-ai's postinstall
# swaps in the platform binary. The symlink keeps `opencode` on PATH as the global
# install did. To move to a newer opencode, change the version in package.json and run
# `npm install --package-lock-only --ignore-scripts` in this directory.
COPY --chown=node:node package.json package-lock.json /opt/agents/
RUN npm --prefix /opt/agents ci && \
    ln -s /opt/agents/node_modules/.bin/opencode /usr/local/share/npm-global/bin/opencode && \
    chmod -R a+rwX /home/node
