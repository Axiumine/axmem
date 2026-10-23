"""Support for the tests of the release flow: scratch worlds, fakes of ``gh``, ``curl``, ``uv`` and ``ssh``, scans.

* ``world``: ``World``, a scratch repository and remote in which the real ``scripts/release-pypi.sh`` runs.
* ``fake_bin/``: the fake ``gh`` (CI, signing and publish runs, release assets, the repository settings), ``curl`` (PyPI:
  the JSON of the project and its files, GET only), ``uv`` and ``ssh`` that ``World`` puts first on ``PATH`` (they are
  copied, never imported); the modules ``_state`` and ``_publish`` are what they share.
* ``fakes``: light fakes of ``gh``, ``curl`` and ``uv`` for the tests of the checks in ``scripts/release`` on their own;
  the ``curl`` and the publishing half of ``gh`` are the full fakes' own code.
* ``script``: lifts the functions of ``scripts/release-pypi.sh`` and ``scripts/lib/release-flow.sh`` out of the text.
* ``upload_scan``: finds every upload command in the code of the repository.
* ``real_uv``: the real ``uv``, to hold the fake ``uv`` build to it.
"""
