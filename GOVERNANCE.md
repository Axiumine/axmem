# Governance

Axmem is a small project with two owners: the lead maintainer, who does the day-to-day work, and a co-owner who is the
successor. This page says who decides what, who may do what, and what happens if the lead maintainer is not available
([If the lead is unavailable](#if-the-lead-is-unavailable)). The people are listed in [MAINTAINERS.md](MAINTAINERS.md).

## Roles

* **Lead maintainer** (owner). Decides what goes into the project and what does not, reviews and
  merges changes, makes releases (see [docs/release.md](docs/release.md)),
  triages security reports (see [SECURITY.md](SECURITY.md)) and answers issues.
* **Co-owner and successor.** Has the same role in the GitHub organization (owner) and the repository (admin) as the lead
  maintainer. Keeps the project running if the lead maintainer cannot: answers issues and security reports and merges
  fixes. Releasing them also needs access that is not shared yet (the PyPI project, the approval of the release
  environments, a machine that passes the release gates); [below](#if-the-lead-is-unavailable) says what is
  missing. The project is AGPL-3.0-only, so anyone may also fork it.
* **Contributors.** Anyone who opens an issue or sends a pull request
  ([CONTRIBUTING.md](CONTRIBUTING.md)). Contributors have no special access.
* **Reporters.** Anyone who reports a bug, a request or a vulnerability. Vulnerabilities are reported
  privately, as [SECURITY.md](SECURITY.md) says, not in a public issue.

## Decisions

Decisions are made in the open, in issues and pull requests. The lead maintainer decides, and
the co-owner is consulted on anything that affects access, releases or security. Choices that shape the design are
written down in [architecture.md](architecture.md), and the reason for each change from Forgetful is in
[docs/changes-from-forgetful.md](docs/changes-from-forgetful.md).

Hardware support is decided: Axmem runs on NVIDIA GPUs only, requests for other devices are not taken, and a pull
request that adds Apple silicon with an MLX backend is the one way to add one
([CONTRIBUTING.md](CONTRIBUTING.md#hardware)).

## Changes and review

Every change from a contributor arrives as a pull request. A maintainer reviews it against
[CONTRIBUTING.md](CONTRIBUTING.md#code-review): it follows the layering (routes, services, protocols, repositories and
adapters), has tests that keep coverage at 100 %, updates the docs and the changelog, and does not weaken a quality gate or a
security boundary (authentication and scopes, the request body limit, the loopback guard). The procedure, the checks that
must be green and the conditions for a merge are in that section.

The maintainers' own changes are pushed to `main` after the git hooks have passed on the maintainer's machine. No second
person reviews them today.

## Access

* Write access is given to a contributor only after several accepted changes and a look at their
  past work (the quality of the changes and how they treated others). Admin access and organization
  ownership stay with the maintainers.
* Everyone with write access must use two-factor authentication, which the `Axiumine` organization
  requires. New write access is given only to accounts that use a TOTP app, a passkey or a security
  key, not SMS.
* Access is removed on request, when someone is inactive for a long time, and at once if an account
  may be compromised.

## If the lead is unavailable

The co-owner takes over. This section says what the co-owner can do today and what only the lead can hand over. A line
marked **To do** is not done; nothing here claims that a hand-over has been tried.

### What the co-owner can do today

The co-owner is an owner of the `Axiumine` organization and an admin of `Axiumine/axmem`. With that role they can:

* answer, label and close issues, review and merge pull requests, and push to `main`;
* change the settings of the repository and the organization: the rulesets for branches and tags, the environments of the
  release workflows, and who has access;
* create a release tag, because the Admin role is the only bypass actor of the tag ruleset on `v*`;
* run the workflows that can be started by hand.

### What needs a hand-over

| What | State today | To do |
|---|---|---|
| The PyPI project `axmem` | The lead is its only owner. A release does not use a PyPI login (the upload is by trusted publishing and no token exists), but an owner manages the project: the trusted publisher, yanking a release, other owners. | Add the co-owner as an owner on PyPI, with their own account and a hardware-key second factor ([docs/release.md](docs/release.md#once-before-the-first-release)). Do the same on TestPyPI if the rehearsal is used. |
| The approval of a release | The lead is the only required reviewer of the GitHub environments `pypi` and `testpypi`, and administrators cannot bypass them. The co-owner can edit the list as an admin, but a release should not wait on one account. | Add the co-owner as a required reviewer of both environments. |
| The machine that releases | A release is the script `scripts/release-pypi.sh` ([docs/release.md](docs/release.md)). Its commit and push run the hooks, so it needs a machine that passes the `pre-push` gates: an NVIDIA GPU (driver R580 or newer), Docker, `uv`, `git`, `gh`, `curl`, `flock`, `timeout`, a fine-grained GitHub token for the repository and the Qodana token of the `qodana` gate. These are on the lead's machine, not in the repository. | The successor needs such a machine and their own logins and token. "Once, before the first release" in [docs/release.md](docs/release.md#once-before-the-first-release) lists the setup. |
| A release without that machine | None is documented. The script refuses to start when `SKIP` or `ONLY` leaves a gate out, and the gates are not weakened to make a change pass. Until a machine is handed over the co-owner can merge a fix but cannot release it. | Decide on an emergency route and write it in [docs/release.md](docs/release.md), or keep a second machine ready. |
| The signing identity | Nothing personal. `release.yml` signs the files keyless in CI, and the identity is that workflow at the tag, not a person or a key. The script makes the release commit and the tag with the git identity of whoever runs it. | None. |
| Secrets | The repository, the organization and the release environments hold no secret. | None. |
| Security reports | They go to the contact named in [SECURITY.md](SECURITY.md). GitHub's private vulnerability reporting is enabled for the repository (read from the GitHub API on 2026-10-29). GitHub tells the administrators of the repository and the security managers of the organization about a new report; the organization has no security manager and no team, so today those are the two owners, the only administrators. Whether a person gets an e-mail depends on their own GitHub notification settings, which nobody else can read. The address in SECURITY.md is still the lead maintainer's alone. | Each owner checks that their notification settings deliver security alerts. Decide whether the co-owner is also named in SECURITY.md as a contact. |
| The rules for `main` | They block deletion and force pushes and require signed commits, a pull request with one approval of a code owner (`CODEOWNERS`), resolved conversations and the checks `lint`, `smoke-test`, `static`, `trivy` and the two CodeQL analyses, green on the last commit. The Admin role is the only bypass actor, because the release script pushes to `main` ([docs/release.md](docs/release.md#once-before-the-first-release)). Both owners are admins, so either can merge without an approval, and the rules bind a contributor, not the maintainers. | A second maintainer who reviews the pull requests of the lead; until then the approval is a formality and the OpenSSF Scorecard check Code-Review stays at 0. |

The release script keeps no state of its own: it reads git, GitHub and PyPI, so it can be run again from another checkout
and continues at the first step that is not done ([docs/release.md](docs/release.md#when-it-stops)). That has not been tried
with a second person. If neither owner can act, the project is AGPL-3.0-only and anyone may fork it.

## Conduct

Everyone in the project's spaces follows the [Code of Conduct](CODE_OF_CONDUCT.md). The maintainers are
the community leaders who enforce it.
