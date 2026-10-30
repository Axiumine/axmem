"""``scripts/smoke-release.sh`` believes the signed tag and not the index: it installs nothing the manifest does not record.

Run against a fake TestPyPI and a fake PyPI (``tests/release_support/smoke_script.py``), the script stops, with nothing installed
and nothing started, when the index does not serve the version or answers for another, lists other files than the manifest,
links a file anywhere but the files host of that index (another host, a suffix of it, the other index's, plain http, a relative
link), lists another digest than the manifest, or serves other bytes than the digest it lists. ``test_release_smoke_script.py``
holds what it refuses before the index is read.
"""

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.release_support.smoke_script import (
    INDEXES,
    REAL_TOOLS,
    SDIST_BYTES,
    SmokeWorld,
    downloads,
    nothing_started,
    refused,
)
from tests.release_support.testpypi_script import SDIST, VERSION, WHEEL

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in REAL_TOOLS), reason=f"needs {' '.join(REAL_TOOLS)}",
)

LINKS = {
    "another-host": "https://evil.example/packages/{name}",
    "a-suffix-of-the-host": "https://{files_host}.evil.example/packages/{name}",
    "the-other-index": "https://{other_files_host}/packages/{name}",
    "the-json-host": "https://{index_host}/packages/{name}",
    "plain-http": "http://{files_host}/packages/{name}",
    "a-relative-link": "{name}",
}
FILE_SETS: dict[str, Callable[[list[dict[str, Any]]], object]] = {
    "a-file-missing": lambda urls: urls.pop(),
    "an-extra-file": lambda urls: urls.append({**urls[0], "filename": "axmem-0.2.0-py2-none-any.whl"}),
    "another-name": lambda urls: urls[0].update(filename="axmem-0.2.1-py3-none-any.whl"),
    "no-file": lambda urls: urls.clear(),
}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[SmokeWorld]:
    made = SmokeWorld(tmp_path)
    yield made
    made.stop_leftovers()


@pytest.mark.parametrize("index", sorted(INDEXES))
class TestTheRefusalsOnceTheIndexIsRead:
    def test_an_index_that_does_not_serve_the_version_is_named(self, world: SmokeWorld, index: str) -> None:
        host = INDEXES[index][0]
        world.configure(index={name: doc for name, doc in world.conf()["index"].items() if name != host})

        done = world.run("--index", index, VERSION)

        refused(done, f"{host} does not serve axmem {VERSION} (is it uploaded?)")
        nothing_started(world)

    def test_an_index_that_answers_for_another_version_is_refused(self, world: SmokeWorld, index: str) -> None:
        document = world.index_document(index)
        document["info"]["version"] = "0.2.1"
        world.edit_index(index, document)

        done = world.run("--index", index, VERSION)

        refused(done, f"{INDEXES[index][0]} answered for another version")
        nothing_started(world)

    @pytest.mark.parametrize("change", FILE_SETS.values(), ids=list(FILE_SETS))
    def test_an_index_that_lists_other_files_than_the_manifest_is_refused_and_nothing_is_downloaded(
        self, world: SmokeWorld, index: str, change: Callable[[list[dict[str, Any]]], object],
    ) -> None:
        document = world.index_document(index)
        change(document["urls"])
        world.edit_index(index, document)

        done = world.run("--index", index, VERSION)

        refused(done, f"{INDEXES[index][0]} lists other files than the manifest. listed: ")
        assert downloads(world) == []
        nothing_started(world)

    @pytest.mark.parametrize("link", LINKS.values(), ids=list(LINKS))
    def test_a_link_that_is_not_on_the_files_host_of_the_index_is_not_followed(
        self, world: SmokeWorld, index: str, link: str,
    ) -> None:
        host, files_host = INDEXES[index]
        other_files_host = INDEXES["pypi" if index == "testpypi" else "testpypi"][1]
        document = world.index_document(index)
        (entry,) = [entry for entry in document["urls"] if entry["filename"] == WHEEL]
        entry["url"] = link.format(name=WHEEL, files_host=files_host, other_files_host=other_files_host, index_host=host)
        world.edit_index(index, document)
        world.serve_file(entry["url"], "bytes from a place that the index does not own")

        done = world.run("--index", index, VERSION)

        refused(done, f"{WHEEL}: its URL is not on {files_host}: {entry['url']}")
        assert downloads(world) == []
        nothing_started(world)

    def test_a_digest_the_index_lists_that_is_not_the_manifest_s_is_refused_before_the_download(
        self, world: SmokeWorld, index: str,
    ) -> None:
        document = world.index_document(index)
        document["urls"][0]["digests"]["sha256"] = "0" * 64
        world.edit_index(index, document)

        done = world.run("--index", index, VERSION)

        refused(done, f"{WHEEL}: {INDEXES[index][0]} lists another sha256 than the manifest")
        assert downloads(world) == []
        nothing_started(world)

    def test_bytes_that_are_not_the_manifest_s_are_refused_whatever_the_index_lists(
        self, world: SmokeWorld, index: str,
    ) -> None:
        document = world.index_document(index)
        (entry,) = [entry for entry in document["urls"] if entry["filename"] == SDIST]
        world.serve_file(entry["url"], SDIST_BYTES + "one byte more")

        done = world.run("--index", index, VERSION)

        refused(done, f"{SDIST}: the downloaded bytes have sha256 ")
        assert "the manifest says" in done.stderr
        nothing_started(world)

    def test_a_download_that_fails_is_named(self, world: SmokeWorld, index: str) -> None:
        (entry,) = [entry for entry in world.index_document(index)["urls"] if entry["filename"] == WHEEL]
        world.configure(files={url: text for url, text in world.conf()["files"].items() if url != entry["url"]})

        done = world.run("--index", index, VERSION)

        refused(done, f"{WHEEL}: the download failed")
        nothing_started(world)
