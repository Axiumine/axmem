# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""VEX gate: the OpenVEX document is well formed, and every accepted finding is explained in it.

``security/axmem.openvex.json`` is an OpenVEX v0.2.0 document (https://github.com/openvex/spec)
for the product ``pkg:pypi/axmem``. It holds a statement only for a vulnerability in a component
that a person decided does not affect Axmem; it is empty until there is one. The gate checks,
with the standard library:

* the shape of the document and of each statement: the fields it needs, the values of ``status``
  and ``justification`` that the specification allows, timestamps in ISO 8601 with a zone, and
  no field it does not know (a typo);
* the link with ``.trivyignore``: every entry still in force (``suppressions.py``) has a
  statement for its identifier, and for each package that the statements name, the latest one
  has status ``not_affected``, a justification and an impact statement. A statement with status
  ``fixed``, ``affected`` or ``under_investigation`` is a record and nothing more: it never
  explains an entry, and an entry whose latest statement for a package is one of them fails,
  because the document then contradicts the suppression;
* the entries of ``.trivyignore`` themselves (a date, a reason, at most 90 days ahead).

The gate checks the file as it is. That ``version`` and ``last_updated`` of the document were
raised when a statement changed is checked in review (docs/quality-gates.md, "Vulnerabilities
that do not affect Axmem"); the gate only holds the dates to each other.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import suppressions
from .core import ROOT, Ctx, Skip

VEX_FILE = "security/axmem.openvex.json"
CONTEXT = "https://openvex.dev/ns/v0.2.0"
PRODUCT = "pkg:pypi/axmem"
STATUSES = frozenset({"not_affected", "affected", "fixed", "under_investigation"})
JUSTIFICATIONS = frozenset({
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
})
DOCUMENT_KEYS = frozenset({
    "@context", "@id", "author", "role", "timestamp", "last_updated", "version", "tooling",
    "statements",
})
STATEMENT_KEYS = frozenset({
    "@id", "version", "vulnerability", "timestamp", "last_updated", "products", "status",
    "supplier", "status_notes", "justification", "impact_statement", "action_statement",
    "action_statement_timestamp",
})
INPUTS = (
    VEX_FILE,
    suppressions.SUPPRESSION_FILE,
    "scripts/quality/vex.py",
    "scripts/quality/suppressions.py",
)
FUTURE_SLACK = timedelta(days=1)
_MOMENTS = ("timestamp", "last_updated")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}")
Statement = dict[str, object]


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _one_of(value: object, options: frozenset[str]) -> bool:
    """Whether a decoded JSON value is one of the strings; a list or an object is none of them."""
    return isinstance(value, str) and value in options


def _moment(value: object) -> datetime | None:
    """An ISO 8601 timestamp with a zone, or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _dates(where: str, item: Statement, now: datetime) -> list[str]:
    """``timestamp`` and ``last_updated`` of a document or a statement.

    Both parse, neither is ahead of now, and ``last_updated`` is not before ``timestamp``.
    """
    problems: list[str] = []
    moments: dict[str, datetime] = {}
    for key in _MOMENTS:
        if key not in item:
            continue
        moment = _moment(item[key])
        if moment is None:
            problems.append(f"{where}: {key} {item[key]!r} is not ISO 8601 with a zone")
        elif moment > now + FUTURE_SLACK:
            problems.append(f"{where}: {key} {item[key]} is in the future")
        else:
            moments[key] = moment
    if "timestamp" not in item:
        problems.append(f"{where}: timestamp is missing")
    if len(moments) == len(_MOMENTS) and moments["last_updated"] < moments["timestamp"]:
        problems.append(f"{where}: last_updated is before timestamp")
    return problems


def _version(where: str, item: Statement, *, required: bool) -> list[str]:
    value = item.get("version")
    if value is None and not required:
        return []
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return []
    return [f"{where}: version {value!r} is not an integer from 1 up"]


def _components(where: str, product: Statement, *, required: bool) -> list[str]:
    """The ``subcomponents`` of a product: the dependency that a statement is about, by purl."""
    parts = product.get("subcomponents") or []
    if not isinstance(parts, list) or (required and not parts):
        return [f"{where}: {product['@id']} has no subcomponents, the dependency in question"]
    if not all(isinstance(part, dict) and _purl(part, "pkg:pypi/") for part in parts):
        return [f"{where}: a subcomponent of {product['@id']} has no pkg:pypi/ @id"]
    return []


def _purl(item: Statement, prefix: str) -> bool:
    return str(item.get("@id", "")).startswith(prefix)


def _product(where: str, product: object, *, with_components: bool) -> list[str]:
    ident = str(product.get("@id")) if isinstance(product, dict) else ""
    if not isinstance(product, dict) or ident.split("@", maxsplit=1)[0] != PRODUCT:
        return [f"{where}: a product is not {PRODUCT} (or {PRODUCT}@<version>): {product!r:.60}"]
    return _components(where, product, required=with_components)


def _products(where: str, products: object, *, with_components: bool) -> list[str]:
    """The product is Axmem; a statement about a dependency names it in ``subcomponents``."""
    if not isinstance(products, list) or not products:
        return [f"{where}: products is not a list with at least one product"]
    return [
        problem
        for product in products
        for problem in _product(where, product, with_components=with_components)
    ]


def _vulnerability(where: str, vulnerability: object) -> list[str]:
    name = vulnerability.get("name") if isinstance(vulnerability, dict) else None
    if isinstance(name, str) and _NAME.fullmatch(name):
        return []
    return [f"{where}: vulnerability.name {name!r} is not an id such as CVE-2026-0001"]


def _verdict(where: str, statement: Statement) -> list[str]:
    """What each status needs: a reason for ``not_affected`` and an action for ``affected``."""
    status = statement.get("status")
    if not _one_of(status, STATUSES):
        return [f"{where}: status {status!r} is none of {', '.join(sorted(STATUSES))}"]
    problems: list[str] = []
    if status == "not_affected":
        if not _one_of(statement.get("justification"), JUSTIFICATIONS):
            options = ", ".join(sorted(JUSTIFICATIONS))
            problems.append(f"{where}: not_affected needs a justification, one of {options}")
        if not _text(statement.get("impact_statement")):
            problems.append(f"{where}: not_affected needs an impact_statement: why not affected")
    elif "justification" in statement:
        problems.append(f"{where}: justification belongs to not_affected, not to {status}")
    if status == "affected" and not _text(statement.get("action_statement")):
        problems.append(f"{where}: affected needs an action_statement (remediation, mitigation)")
    return problems


def check_statement(number: int, statement: object, now: datetime) -> list[str]:
    where = f"statements[{number}]"
    if not isinstance(statement, dict):
        return [f"{where}: not an object"]
    unknown = sorted(set(statement) - STATEMENT_KEYS)
    problems = [f"{where}: unknown field {key!r}" for key in unknown]
    problems += _vulnerability(where, statement.get("vulnerability"))
    problems += _dates(where, statement, now)
    problems += _version(where, statement, required=False)
    components = statement.get("status") == "not_affected"
    problems += _products(where, statement.get("products"), with_components=components)
    return problems + _verdict(where, statement)


def _header(document: Statement, now: datetime) -> list[str]:
    """The fields of the document other than the statements."""
    problems = [f"unknown field {key!r}" for key in sorted(set(document) - DOCUMENT_KEYS)]
    if document.get("@context") != CONTEXT:
        problems.append(f"@context is not {CONTEXT}")
    ident = document.get("@id")
    if not isinstance(ident, str) or not ident.startswith("https://"):
        problems.append(f"@id {ident!r} is not an https IRI")
    if not _text(document.get("author")):
        problems.append("author is missing")
    problems += _dates("document", document, now)
    return problems + _version("document", document, required=True)


def check_document(document: object, now: datetime) -> tuple[list[str], list[Statement]]:
    """The problems with the shape of the document, and its statements that have none."""
    if not isinstance(document, dict):
        return [f"{VEX_FILE}: the document is not a JSON object"], []
    problems = _header(document, now)
    statements = document.get("statements")
    if not isinstance(statements, list):
        return [*problems, "statements is not a list (an empty list is fine)"], []
    sound: list[Statement] = []
    for number, statement in enumerate(statements):
        found = check_statement(number, statement, now)
        problems += found
        if not found and isinstance(statement, dict):
            sound.append(statement)
    return [*problems, *_ordering(document, sound)], sound


def _latest(statement: Statement) -> datetime:
    """When a statement was last said: ``last_updated``, else ``timestamp``."""
    moment = _moment(statement.get("last_updated")) or _moment(statement.get("timestamp"))
    return moment or datetime.min.replace(tzinfo=UTC)


def _ordering(document: Statement, statements: list[Statement]) -> list[str]:
    """The document was updated at or after its newest statement."""
    stamped = _moment(document.get("last_updated")) or _moment(document.get("timestamp"))
    newest = max((_latest(statement) for statement in statements), default=None)
    if stamped and newest and stamped < newest:
        return ["document: last_updated is before the newest statement (raise it with version)"]
    return []


def _packages(statement: Statement) -> list[str]:
    """What a statement is about, as purls without version: its subcomponents, else its products."""
    products = statement.get("products")
    found: list[str] = []
    for product in products if isinstance(products, list) else []:
        parts = product.get("subcomponents") or [product]
        found += [re.split(r"[@?#]", str(part["@id"]), maxsplit=1)[0] for part in parts]
    return found


def deciding(statements: list[Statement]) -> dict[tuple[str, str], Statement]:
    """For each vulnerability and package the statement that spoke last.

    The later timestamp wins; of two at the same time, the one further down the list. An entry of
    ``.trivyignore`` accepts an identifier for every package, so a later ``not_affected`` for one
    package must not hide an ``affected`` for another.
    """
    latest: dict[tuple[str, str], Statement] = {}
    for statement in sorted(statements, key=_latest):
        vulnerability = statement["vulnerability"]
        if isinstance(vulnerability, dict):
            for package in _packages(statement):
                latest[str(vulnerability["name"]), package] = statement
    return latest


def link_problems(
    statements: list[Statement], entries: list[suppressions.Suppression], today: date,
) -> list[str]:
    """Every entry of ``.trivyignore`` in force has a ``not_affected`` statement, last word."""
    latest = deciding(statements)
    problems: list[str] = []
    for entry in entries:
        if not entry.active(today) or entry.ident.startswith(suppressions.UNCHECKED_PREFIX):
            continue
        where = f"{suppressions.SUPPRESSION_FILE}:{entry.line}"
        verdicts = {
            package: statement.get("status")
            for (name, package), statement in latest.items()
            if name == entry.ident
        }
        if not verdicts:
            problems.append(f"{where}: {entry.ident} has no statement in {VEX_FILE}")
        problems += [
            f"{where}: the latest statement for {entry.ident} is {status} for {package}, "
            f"so the entry contradicts {VEX_FILE}"
            for package, status in verdicts.items()
            if status != "not_affected"
        ]
    return problems


def run_vex(
    root: Path, today: date | None = None, now: datetime | None = None,
) -> tuple[bool, str]:
    """The gate over the document and ``.trivyignore`` under ``root``."""
    moment = now or datetime.now(UTC)
    day = today or moment.date()
    entries, problems = suppressions.read(root, day)
    try:
        document = json.loads((root / VEX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as why:  # no file, bad UTF-8, bad JSON, too deep
        return False, "\n".join([*problems, f"{VEX_FILE} cannot be read: {why}"])
    shape, statements = check_document(document, moment)
    problems += [f"{VEX_FILE}: {problem}" for problem in shape]
    problems += link_problems(statements, entries, day)
    if problems:
        return False, "\n".join(problems)
    return True, f"{VEX_FILE}: {len(statements)} statements, {len(entries)} accepted findings"


def gate_vex(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.touches(*INPUTS):
        raise Skip("neither the VEX document nor the accepted findings are staged")
    return run_vex(ROOT)
