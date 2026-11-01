# Governance

Axmem is a small project with one maintainer, the lead maintainer, who does the day-to-day work. There is no successor
and no co-maintainer, so the bus factor is 1. This page says who decides what, who may do what, and what happens if the
lead maintainer is not available ([If the lead is unavailable](#if-the-lead-is-unavailable)). The maintainer, and the
other accounts that have access to the sensitive resources, are listed in [MAINTAINERS.md](MAINTAINERS.md).

## Roles

* **Lead maintainer** (owner). Decides what goes into the project and what does not, reviews and
  merges changes, makes releases (see [docs/release.md](docs/release.md)),
  triages security reports (see [SECURITY.md](SECURITY.md)) and answers issues.
* **Accounts with access that are not maintainers.** The GitHub account @vivi7 is an owner of the `Axiumine` organization
  and an admin of the repository. It is not a maintainer: it has no role in decisions, and none in releases is
  documented. It is listed in [MAINTAINERS.md](MAINTAINERS.md#other-accounts-with-access) because it has access to the
  sensitive resources of the project.
* **Contributors.** Anyone who opens an issue or sends a pull request
  ([CONTRIBUTING.md](CONTRIBUTING.md)). Contributors have no special access.
* **Reporters.** Anyone who reports a bug, a request or a vulnerability. Vulnerabilities are reported
  privately, as [SECURITY.md](SECURITY.md) says, not in a public issue.

## Decisions

Decisions are made in the open, in issues and pull requests. The lead maintainer decides; there is no second maintainer
to consult or to overrule a decision. Choices that shape the design are written down in
[architecture.md](architecture.md), and the reason for each change from Forgetful is in
[docs/changes-from-forgetful.md](docs/changes-from-forgetful.md).

Hardware support is decided: Axmem runs on NVIDIA GPUs only, requests for other devices are not taken, and a pull
request that adds Apple silicon with an MLX backend is the one way to add one
([CONTRIBUTING.md](CONTRIBUTING.md#hardware)).

## Changes and review

Every change from a contributor arrives as a pull request. The lead maintainer reviews it against
[CONTRIBUTING.md](CONTRIBUTING.md#code-review): it follows the layering (routes, services, protocols, repositories and
adapters), has tests that keep coverage at 100 %, updates the docs and the changelog, and does not weaken a quality gate or a
security boundary (authentication and scopes, the request body limit, the loopback guard). The procedure, the checks that
must be green and the conditions for a merge are in that section.

The lead maintainer's own changes are pushed to `main` after the git hooks have passed on the maintainer's machine. No
second person reviews them: the project has no second maintainer.

## Access

* Write access is given to a contributor only after several accepted changes and a look at their
  past work (the quality of the changes and how they treated others). Admin access and organization
  ownership are not given to contributors. Today two accounts have them: the lead maintainer and @vivi7, which is not
  a maintainer ([MAINTAINERS.md](MAINTAINERS.md#other-accounts-with-access)).
* Everyone with write access must use two-factor authentication, which the `Axiumine` organization
  requires. New write access is given only to accounts that use a TOTP app, a passkey or a security
  key, not SMS.
* Access is removed on request, when someone is inactive for a long time, and at once if an account
  may be compromised.
* The accounts that have access to the sensitive resources (the GitHub repository and organization, the PyPI project
  `axmem`) are listed in [MAINTAINERS.md](MAINTAINERS.md), with the role of each. A person is added to that list when
  they are given such access.
* The credentials the project uses, who may use them and how they are stored, rotated and revoked are in
  [Secrets and credentials](docs/security.md#secrets-and-credentials).

## If the lead is unavailable

Nobody is named to take over. The project has one maintainer, no successor and no co-maintainer, and the bus factor is 1.
This section says what follows from that: what the other admin account can do through its GitHub role, and what only the
lead holds. Nothing here claims that a hand-over has been tried or planned. For this reason the OpenSSF Best Practices
criteria on bus factor, access continuity and two-person review are answered Unmet in the badge entry of the project
(`.bestpractices.json` leaves out the answers that depend on a person).

### What the other admin account can do

The account @vivi7 is an owner of the `Axiumine` organization and an admin of `Axiumine/axmem` (read from the GitHub
API on 2026-11-01). It is not a maintainer. What follows is what the GitHub role allows. It is access, not a role in
the project, and it is not a succession plan. With the Admin role the account can:

* answer, label and close issues, review and merge pull requests, and push to `main`, because the Admin role bypasses the
  rules for `main`;
* change the settings of the repository and the organization: the rulesets for branches and tags, the environments of the
  release workflows, and who has access;
* create a release tag, because the Admin role is the only bypass actor of the tag ruleset on `v*`;
* read the private vulnerability reports of the repository, which GitHub shows to its administrators.

It cannot approve a release: the lead is the only required reviewer of the release environments, and administrators
cannot bypass them. It has no access to the PyPI project.

### What needs a hand-over

Nobody is named to receive these. The table says what only the lead holds and what follows if the lead is unavailable.

| What | State today | If the lead is unavailable |
|---|---|---|
| The PyPI project `axmem` | The lead is its only owner. A release does not use a PyPI login (the upload is by trusted publishing and no token exists), but an owner manages the project: the trusted publisher, yanking a release, other owners. | Nobody else can manage the project on PyPI or yank a release. Adding an owner (with their own account and a hardware-key second factor, see [docs/release.md](docs/release.md#once-before-the-first-release)) is not done and nobody is named for it. |
| The approval of a release | The lead is the only required reviewer of the GitHub environments `pypi` and `testpypi`, and administrators cannot bypass them. | No release can be approved. An administrator can edit the list of reviewers, but that is a capability of the GitHub role, not a documented route. |
| The machine that releases | A release is the script `scripts/release-pypi.sh` ([docs/release.md](docs/release.md)). Its commit and push run the hooks, so it needs a machine that passes the `pre-push` gates: an NVIDIA GPU (driver R580 or newer), Docker, `uv`, `git`, `gh`, `curl`, `flock`, `timeout`, a fine-grained GitHub token for the repository and the Qodana token of the `qodana` gate. These are on the lead's machine, not in the repository. | Whoever released next would need such a machine and their own logins and token. "Once, before the first release" in [docs/release.md](docs/release.md#once-before-the-first-release) lists the setup. |
| A release without that machine | None is documented. The script refuses to start when `SKIP` or `ONLY` leaves a gate out, and the gates are not weakened to make a change pass. | A fix can be merged by an administrator but not released. No emergency route is written in [docs/release.md](docs/release.md). |
| The signing identity | Nothing personal. `release.yml` signs the files keyless in CI, and the identity is that workflow at the tag, not a person or a key. The script makes the release commit and the tag with the git identity of whoever runs it. | Nothing to hand over. |
| Secrets | The repository, the organization and the release environments hold no secret. | Nothing to hand over. |
| Security reports | They go to the contact named in [SECURITY.md](SECURITY.md), which is the lead maintainer's address alone. GitHub's private vulnerability reporting is enabled for the repository (read from the GitHub API on 2026-10-29). GitHub shows a report to the administrators of the repository and the security managers of the organization; the organization has no security manager and no team, so today the readers are the lead and the account @vivi7, the only administrators. Whether a person gets an e-mail depends on their own GitHub notification settings, which nobody else can read. | A report may wait unanswered. [SECURITY.md](SECURITY.md) tells the reporter to write again on the same private channel after 14 days and then to open a public issue titled "Security contact request", with no details. Nobody is named to answer it. |
| The rules for `main` | They block deletion and force pushes and require signed commits, a pull request with one approval of a code owner (`CODEOWNERS`), resolved conversations and the checks `lint`, `smoke-test`, `static`, `trivy` and the two CodeQL analyses, green on the last commit. The lead is the only code owner and cannot approve their own pull request. The Admin role is the only bypass actor, because the release script pushes to `main` and the lead merges their own changes that way ([docs/release.md](docs/release.md#once-before-the-first-release)). The Admin role includes the account @vivi7, which is not a maintainer. | The rules bind a contributor, not the lead. Nobody reviews the lead's pull requests, so the approval is a formality for them and the OpenSSF Scorecard check Code-Review stays at 0. |

The release script keeps no state of its own: it reads git, GitHub and PyPI, so it can be run again from another checkout
and continues at the first step that is not done ([docs/release.md](docs/release.md#when-it-stops)). That has not been tried
with a second person. If the lead cannot act, nobody is named to act in their place; the project is AGPL-3.0-only and
anyone may fork it.

## Conduct

Everyone in the project's spaces follows the [Code of Conduct](CODE_OF_CONDUCT.md). The lead maintainer is
the community leader who enforces it.
