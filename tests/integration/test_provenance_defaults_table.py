"""Integration tests for what the environment settings do to the provenance of a memory (app/utils/provenance.py)

``ENCODING_*`` and ``AGENT_*`` name who wrote a memory. They fill what a create request leaves empty and, when
``ENFORCE_ENV_OVERWRITE`` is on, replace what the client wrote on a create and on an update. A setting that is empty (or
does not exist) changes nothing, and an update never gains a field that the client did not send.
"""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.models.memory_models import MemoryCreate, MemoryUpdate
from app.utils import provenance
from app.utils.provenance import (
    PROVENANCE_ENV_MAP,
    apply_provenance_defaults,
    apply_provenance_defaults_for_update,
)

FIELDS = list(PROVENANCE_ENV_MAP)
ENV = {
    "ENCODING_AGENT": "env-encoder",
    "ENCODING_VERSION": "env-encoder-1",
    "AGENT_ID": "env-agent",
    "AGENT_VERSION": "env-agent-1",
    "AGENT_MODEL": "env-model",
}
CLIENT = {
    "encoding_agent": "client-encoder",
    "encoding_version": "client-encoder-1",
    "agent_id": "client-agent",
    "agent_version": "client-agent-1",
    "agent_model": "client-model",
}


def _settings(monkeypatch, *, enforce: bool, **values: str) -> None:
    """The settings the module reads: only the named ones exist."""
    monkeypatch.setattr(provenance, "settings", SimpleNamespace(ENFORCE_ENV_OVERWRITE=enforce, **values))


def _create(**provenance_fields) -> MemoryCreate:
    return MemoryCreate(
        title="Probe", content="A memory", context="provenance table", keywords=["k"], tags=["t"], **provenance_fields,
    )


def _provenance(model) -> dict:
    return {field: getattr(model, field) for field in FIELDS}


class _Bare(BaseModel):
    """A model that has none of the provenance fields."""

    title: str


def test_the_map_names_the_setting_of_each_provenance_field():
    assert PROVENANCE_ENV_MAP == {
        "encoding_agent": "ENCODING_AGENT",
        "encoding_version": "ENCODING_VERSION",
        "agent_id": "AGENT_ID",
        "agent_version": "AGENT_VERSION",
        "agent_model": "AGENT_MODEL",
    }


# ============================================
# create
# ============================================


def test_the_environment_fills_every_field_that_a_create_request_left_empty(monkeypatch):
    _settings(monkeypatch, enforce=False, **ENV)

    result = apply_provenance_defaults(_create())

    assert _provenance(result) == {field: ENV[PROVENANCE_ENV_MAP[field]] for field in FIELDS}


def test_the_environment_fills_only_the_fields_that_are_empty_unless_it_is_enforced(monkeypatch):
    _settings(monkeypatch, enforce=False, **ENV)

    result = apply_provenance_defaults(_create(agent_id="client-agent", encoding_version="client-encoder-1"))

    assert _provenance(result) == {
        "encoding_agent": "env-encoder",
        "encoding_version": "client-encoder-1",
        "agent_id": "client-agent",
        "agent_version": "env-agent-1",
        "agent_model": "env-model",
    }


def test_an_enforced_environment_replaces_what_the_client_wrote_on_a_create(monkeypatch):
    _settings(monkeypatch, enforce=True, **ENV)

    result = apply_provenance_defaults(_create(**CLIENT))

    assert _provenance(result) == {field: ENV[PROVENANCE_ENV_MAP[field]] for field in FIELDS}


def test_an_enforced_environment_also_fills_the_empty_fields_of_a_create(monkeypatch):
    _settings(monkeypatch, enforce=True, **ENV)

    result = apply_provenance_defaults(_create())

    assert _provenance(result) == {field: ENV[PROVENANCE_ENV_MAP[field]] for field in FIELDS}


@pytest.mark.parametrize("enforce", [False, True])
def test_a_setting_that_is_empty_changes_nothing_on_a_create(monkeypatch, enforce):
    _settings(monkeypatch, enforce=enforce, **dict.fromkeys(ENV, ""))
    data = _create(agent_id="client-agent")

    result = apply_provenance_defaults(data)

    assert result is data
    assert _provenance(result) == {**dict.fromkeys(FIELDS), "agent_id": "client-agent"}


@pytest.mark.parametrize("enforce", [False, True])
def test_a_setting_that_does_not_exist_changes_nothing_on_a_create(monkeypatch, enforce):
    _settings(monkeypatch, enforce=enforce)
    data = _create()

    assert apply_provenance_defaults(data) is data


@pytest.mark.parametrize("enforce", [False, True])
def test_only_the_settings_that_exist_and_hold_a_value_apply_on_a_create(monkeypatch, enforce):
    _settings(monkeypatch, enforce=enforce, ENCODING_AGENT="env-encoder", AGENT_ID="")

    result = apply_provenance_defaults(_create(agent_id="client-agent"))

    assert _provenance(result) == {**dict.fromkeys(FIELDS), "encoding_agent": "env-encoder", "agent_id": "client-agent"}


def test_the_model_that_was_given_is_not_modified_by_a_create(monkeypatch):
    _settings(monkeypatch, enforce=True, **ENV)
    data = _create(**CLIENT)

    apply_provenance_defaults(data)

    assert _provenance(data) == CLIENT


@pytest.mark.parametrize("enforce", [False, True])
def test_a_model_without_the_provenance_fields_takes_the_environment_as_if_they_were_empty(monkeypatch, enforce):
    _settings(monkeypatch, enforce=enforce, AGENT_ID="env-agent")

    result = apply_provenance_defaults(_Bare(title="Probe"))

    assert result.title == "Probe"
    assert result.agent_id == "env-agent"


# ============================================
# update
# ============================================


@pytest.mark.parametrize("sent", [{}, {"agent_id": "client-agent"}, CLIENT])
def test_an_update_is_left_as_it_is_when_the_environment_is_not_enforced(monkeypatch, sent):
    _settings(monkeypatch, enforce=False, **ENV)
    data = MemoryUpdate(**sent)

    result = apply_provenance_defaults_for_update(data)

    assert result is data
    assert result.model_fields_set == set(sent)


def test_an_enforced_environment_replaces_each_provenance_field_that_the_update_sent(monkeypatch):
    _settings(monkeypatch, enforce=True, **ENV)

    result = apply_provenance_defaults_for_update(MemoryUpdate(**CLIENT))

    assert _provenance(result) == {field: ENV[PROVENANCE_ENV_MAP[field]] for field in FIELDS}


def test_an_enforced_environment_adds_no_field_that_the_update_did_not_send(monkeypatch):
    _settings(monkeypatch, enforce=True, **ENV)

    result = apply_provenance_defaults_for_update(MemoryUpdate(agent_id="client-agent", title="New title"))

    assert _provenance(result) == {**dict.fromkeys(FIELDS), "agent_id": "env-agent"}
    assert result.model_fields_set == {"agent_id", "title"}
    assert result.title == "New title"


def test_an_update_that_sends_no_provenance_is_returned_as_it_is_even_when_enforced(monkeypatch):
    _settings(monkeypatch, enforce=True, **ENV)
    data = MemoryUpdate(title="New title")

    assert apply_provenance_defaults_for_update(data) is data


def test_a_setting_that_is_empty_leaves_the_field_of_the_update_as_it_was_sent(monkeypatch):
    _settings(monkeypatch, enforce=True, ENCODING_AGENT="", AGENT_ID="env-agent")
    data = MemoryUpdate(encoding_agent="client-encoder", agent_id="client-agent")

    result = apply_provenance_defaults_for_update(data)

    assert (result.encoding_agent, result.agent_id) == ("client-encoder", "env-agent")


def test_a_setting_that_does_not_exist_leaves_the_update_as_it_was_sent(monkeypatch):
    _settings(monkeypatch, enforce=True)
    data = MemoryUpdate(**CLIENT)

    assert apply_provenance_defaults_for_update(data) is data

