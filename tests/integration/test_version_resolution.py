"""``get_version`` resolution order: installed package metadata, generated file, dev fallback."""

import builtins
import importlib.metadata
import sys
import types

from app import version as version_module


def _package_not_installed(name: str) -> str:
    raise importlib.metadata.PackageNotFoundError(name)


def test_installed_package_version_wins(monkeypatch):
    monkeypatch.setattr(version_module, "version", lambda name: "9.8.7" if name == "axmem" else "?")

    assert version_module.get_version() == "9.8.7"


def test_generated_version_file_is_used_when_package_is_not_installed(monkeypatch):
    monkeypatch.setattr(version_module, "version", _package_not_installed)
    generated = types.ModuleType("app._version")
    generated.__version__ = "1.2.3.dev4"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app._version", generated)

    assert version_module.get_version() == "1.2.3.dev4"


def test_dev_placeholder_is_returned_when_no_version_source_exists(monkeypatch):
    monkeypatch.setattr(version_module, "version", _package_not_installed)
    monkeypatch.delitem(sys.modules, "app._version", raising=False)
    real_import = builtins.__import__

    def import_without_generated_file(name, *args, **kwargs):
        if name == "app._version":
            raise ImportError("app._version is not generated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_generated_file)

    assert version_module.get_version() == "0.0.0-dev"

