# Governance

Axmem is a small project with two maintainers. This page says who decides what, who may
do what, and what happens if the lead maintainer is not available. The people are listed in
[MAINTAINERS.md](MAINTAINERS.md).

## Roles

* **Lead maintainer** (owner). Decides what goes into the project and what does not, reviews and
  merges changes, makes releases (see [docs/contributors.md](docs/contributors.md#creating-a-release)),
  triages security reports (see [CONTRIBUTING.md](CONTRIBUTING.md#ways-to-contribute)) and answers issues.
* **Co-owner and successor.** Has the same access to the GitHub organization and repository as the lead
  maintainer (access to the PyPI project is added separately). Keeps the project running if the lead
  maintainer cannot: answers security reports, merges and releases fixes. The project is AGPL-3.0-only,
  so anyone may also fork it.
* **Contributors.** Anyone who opens an issue or sends a pull request
  ([CONTRIBUTING.md](CONTRIBUTING.md)). Contributors have no special access.
* **Reporters.** Anyone who reports a bug, a request or a vulnerability. Vulnerabilities are reported
  privately, as [CONTRIBUTING.md](CONTRIBUTING.md#ways-to-contribute) says, not in a public issue.

## Decisions

Decisions are made in the open, in issues and pull requests. The lead maintainer decides, and
the co-owner is consulted on anything that affects access, releases or security. Choices that shape the design are
written down in [architecture.md](architecture.md), and the reason for each change from Forgetful is in
[docs/changes-from-forgetful.md](docs/changes-from-forgetful.md).

Hardware support is decided: Axmem runs on NVIDIA GPUs only, requests for other devices are not taken, and a pull
request with code that works natively on macOS is the one way to add one
([CONTRIBUTING.md](CONTRIBUTING.md#hardware-nvidia-only)).

## Changes and review

Every change from a contributor arrives as a pull request. A maintainer reviews it against
[CONTRIBUTING.md](CONTRIBUTING.md): it follows the layering (routes, services, protocols, repositories and adapters), has
tests that keep coverage at 100 %, updates the docs and the changelog, and does not weaken a quality gate or a
security boundary (authentication and scopes, the request body limit, the loopback guard). The maintainers' own changes are
checked by the git hooks before they are pushed; a second reviewer is not required for them yet.

## Access

* Write access is given to a contributor only after several accepted changes and a look at their
  past work (the quality of the changes and how they treated others). Admin access and organization
  ownership stay with the maintainers.
* Everyone with write access must use two-factor authentication, which the `Axiumine` organization
  requires. New write access is given only to accounts that use a TOTP app, a passkey or a security
  key, not SMS.
* Access is removed on request, when someone is inactive for a long time, and at once if an account
  may be compromised.

## Conduct

Everyone in the project's spaces follows the [Code of Conduct](CODE_OF_CONDUCT.md). The maintainers are
the community leaders who enforce it.
