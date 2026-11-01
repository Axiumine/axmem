# Security policy

Axmem stores what AI agents remember, often including private work, so a vulnerability can expose real data. This page says
how to report a vulnerability, what to expect afterwards, and how disclosure works. What Axmem promises about its own security
(and what it does not) is in [docs/security.md](docs/security.md); the argument that those promises hold is in
[docs/assurance-case.md](docs/assurance-case.md). How the project stores, uses and rotates its own credentials, and what it
does if one leaks, is in [Secrets and credentials](docs/security.md#secrets-and-credentials).

## Supported versions

| Version | Receives security fixes |
|---|---|
| The 0.6 line: `0.6.0rc1`, the first release and a release candidate, and any later 0.6 release | Yes, the latest release of the line |
| `main` | Fixes land here first, then ship as the next release |
| Version numbers below `0.6.0rc1` (the tags `v0.1.9` to `v0.5.5`) | No. They belong to [Forgetful](https://github.com/ScottRBK/forgetful), the project Axmem is derived from, not to an Axmem release. Report them there |

There are no maintenance branches and no backports. A fix is made on `main` and shipped as a new version of the package.
PyPI never accepts a file name twice, so a fix is always a new version number ([docs/release.md](docs/release.md)).

## Reporting a vulnerability

Do not open a public issue, pull request or discussion for a vulnerability, and do not post details anywhere public.

1. **Preferred: a private vulnerability report on GitHub.** Private vulnerability reporting is enabled for this repository.
   Open <https://github.com/Axiumine/axmem/security/advisories/new>. The report stays private to you and to the
   administrators of the repository, and GitHub shows it to all of them. Today those are the lead maintainer and the account
   [@vivi7](https://github.com/vivi7), which has admin access but is not a maintainer of Axmem ([MAINTAINERS.md](MAINTAINERS.md);
   read from the GitHub API on 2026-11-01). The report is encrypted in transit, and the fix, the advisory and the credit can
   be worked on in the same place.
2. **Or write to dev@giovannimanzoni.com.** This is the lead maintainer's address, the one in
   [CONTRIBUTING.md](CONTRIBUTING.md#ways-to-contribute). It has no published PGP key, so ordinary e-mail encryption
   cannot be assumed: if the details must not travel in clear text, send a short message without them and ask for another
   channel.

### What to include

* The Axmem version or commit, and how it was installed.
* How it is deployed: `AUTH_MODE`, transport (stdio or HTTP), bind address, whether a reverse proxy or tunnel is in front,
  and the Qdrant setup if it matters.
* What an attacker needs to start with (no credential, a valid token of one tenant, a web page open in the operator's
  browser, access to the host, ...).
* The steps to reproduce it, or a proof of concept, and what you observed against what you expected.
* The impact as you see it, and which statement of [docs/security.md](docs/security.md) it breaks, if you can tell.
* Whether and how you want to be credited.

Please never include a real token, an API key or the content of someone's memories. Use throwaway values.

## What to expect

Axmem has one maintainer, the lead maintainer ([MAINTAINERS.md](MAINTAINERS.md)). These times are the lead
maintainer's targets, not guarantees:

| Step | Target |
|---|---|
| Acknowledge that the report arrived | Within 7 days |
| Tell you whether it is confirmed, needs more information or is rejected, and the plan for a fix | Within 14 days |
| After that | Updates as the fix progresses, and a note when it is released |

If a report is rejected we say why. If you disagree, say so in the same thread and we will look again.

## What happens next

1. **Triage.** The maintainer reproduces the problem, decides how severe it is, and checks which versions are affected.
2. **Fix.** The fix is prepared privately, with a test that fails without it, and passes the same gates as every other change
   ([docs/quality-gates.md](docs/quality-gates.md)).
3. **Release.** The fix ships as a new version through the signed release process in [docs/release.md](docs/release.md).
4. **Publication.** For a confirmed vulnerability we publish a GitHub security advisory and request a CVE identifier
   through it. The [CHANGELOG](CHANGELOG.md#security) entry and the release notes name the vulnerability that was fixed, the
   versions affected and the version that fixes it. Users are told to upgrade; if a released version is dangerous to keep, we
   may also yank it on PyPI.

## Coordinated disclosure

We ask you to keep the vulnerability confidential until a fix is released or 90 days have passed since your report,
whichever comes first. We will not ask for more than that without your agreement, and we will tell you if we need more time
and why. If the vulnerability is already being exploited, or you find that it is public, tell us at once: the disclosure
date can then be brought forward. If 90 days pass without a fix, you are free to publish what you found.

## Credit

Anyone who reports a vulnerability that we confirm is named in the advisory and in the changelog entry, with the name or
handle they choose, unless they ask to stay anonymous. Axmem has no bug bounty and pays no rewards.

## If the lead maintainer does not answer

If you have heard nothing after 14 days, write again on the same private channel: add a comment to the private report on
GitHub, or send a second message to the e-mail address above. If that gets no answer either, open a public issue titled
"Security contact request", with no details of the vulnerability, asking the maintainer to contact you.

Nobody else is named to take over the reports: Axmem has one maintainer and no successor. The account @vivi7 can read a
private report on GitHub, because it is an administrator of the repository, but it is not a maintainer, no role of it in
handling reports is documented, and no response time is promised for it. The response times above are the lead
maintainer's.

## What is in scope

* The Axmem server and command line in this repository (`app/`, `main.py`): authentication, scopes, tenant isolation,
  the request limits, the loopback guard, the handling of secrets and any way to read, change or delete another tenant's
  memories or to run code on the server.
* The packages published to PyPI as `axmem`, and the build and release pipeline that makes them (`.github/workflows/`,
  `scripts/`): a way to publish, replace or tamper with a release.
* Documentation that tells an operator to do something insecure.

## What is out of scope

* A vulnerability in a dependency, in Qdrant, in TensorRT, CUDA or the NVIDIA driver, in a model, or in a service that
  Axmem can be pointed at (an identity provider, an embeddings provider). Report it to that project. Tell us too if Axmem is
  affected, so that we can update the lock file. How the project handles such a finding is in
  [Dependency findings](docs/quality-gates.md#dependency-findings).
* What [docs/security.md](docs/security.md) lists under "What Axmem does not guarantee": plain HTTP by default (the server
  serves TLS itself only when `SERVER_SSL_CERTFILE` and `SERVER_SSL_KEYFILE` are set, see
  [Serve TLS](docs/configuration.md#serve-tls); otherwise put a TLS-terminating proxy in front; the security headers are
  always on), no rate limiting, no encryption of stored memories, a Qdrant API key that admits every tenant, prompt
  injection through the content of a memory.
* A deployment that ignores the documented requirements, for example `AUTH_MODE=none` behind a proxy or a port forward, or
  `CORS_ORIGINS=["*"]` on a server whose tokens are handled by pages you do not control.
* Attacks that need control of the host, of the Qdrant node or its API key, of the operator's account, or of the identity
  provider.
* Findings with no demonstrated impact (the output of a scanner, a missing header on a response that carries no
  secret), denial of service by volume alone, and social engineering.

## Checking what you install

Every release is signed and carries a provenance attestation. To verify the files before installing them, see
[Checking a release](docs/release.md#checking-a-release). The security properties of the server are described in
[docs/security.md](docs/security.md).
