# Security review

A review of Axmem's security requirements and of its security boundary, with the findings it produced. The requirements it
reviews are in [docs/security.md](security.md), the argument that they hold is in the [assurance case](assurance-case.md), and
how to report a vulnerability is in [SECURITY.md](../SECURITY.md).

| | |
|---|---|
| Date | 2026-10-29 |
| Version reviewed | `0.6.0rc1` plus the unreleased changes of the [changelog](../CHANGELOG.md) |
| Tree reviewed | Commit `e2cc68b` plus the uncommitted working tree of that day. The unreleased work was not committed yet (it includes the TLS option, `app/config/tls.py`, and the security headers, `app/middleware/security_headers.py`), and a parallel change was adding licence headers to the source files while this was written, so the line numbers below include those headers. Once the work is committed, the maintainer should put the commit here. |
| Prepared by | An AI coding agent (Claude Code, model `claude-sonnet-5-5`) working for the lead maintainer |
| Independent human review | **No.** See the next section |
| Adopted by | Giovanni Manzoni, lead maintainer, on 2026-10-31 (see the next section) |
| Status | Adopted by the lead maintainer as the project's record of its security review; the findings and their statuses are as of the date above |

## Read this first: who wrote this, and what it is worth

This review was prepared by an AI coding agent working for the lead maintainer, as part of the preparation for the gold level of the
OpenSSF Best Practices badge. It is **not** the work of an independent human security reviewer, and it must not be presented as one. The agent read the source, ran the tools listed
below, wrote small probes to test suspicions, and wrote down what it found. It can miss things a person with a different
background would see, and it can be wrong about a line it read. Every claim names a file and a line (or a command) so that a
person can check it, and a claim that was not tested says so.

What had to happen before this page could be used to answer the security-review criterion of the OpenSSF Best Practices
badge (the criterion that asks for a review of the security requirements and of the security boundary), and what is still open:

1. **Done.** The lead maintainer, Giovanni Manzoni, **adopted** this page on 2026-10-31: they take it as the project's own
   record of its security review, with its findings and their statuses as written. The open findings stay open until a change
   fixes them or the maintainer accepts them. Adopting is not redoing the review, and it does not turn the work of the agent
   into an independent review.
2. Whether a review by a project member, or by an AI agent that a project member directed, is enough for that criterion is
   the maintainer's reading of the criterion to make. This page does not claim it is.
3. **A second human reviewer is an open item.** No independent review of the code is on record, and the co-owner has made no
   commit yet ([MAINTAINERS.md](../MAINTAINERS.md)). A reviewer who is not the author of the code is the point of a review.

## Scope

In scope: the Axmem server (`app/`, `main.py`), its HTTP transport, the MCP tools, the REST routes, the CLI, the operator
commands (`backup`, `restore`, `export`, `import`, `re-embed`), the connections to Qdrant, to embeddings and rerank providers,
to identity providers and to Hugging Face, the model and engine files on disk, the log output, the settings, and the GitHub workflows. The test harness and the release scripts were
covered by the tools only, not read.

Out of scope, as in [docs/security.md](security.md#what-is-out-of-scope): the operator's host, Qdrant itself, the identity
provider you choose, and what a user does with a token. Also not reviewed: the source of FastMCP, Starlette, uvicorn,
Qdrant, TensorRT and the other dependencies (only their documented behaviour and, where a finding needed it, a few functions of
FastMCP), and the GitHub settings that are not files (they are listed in [security.md](security.md#supply-chain)).

## Method

### What was read

The functions and modules below were read by the agent on 2026-10-29: the named function in full, and what it calls where that
mattered. The adapters under `app/routes/mcp/adapters/`, `app/repositories/qdrant/memory_search.py`, `app/repositories/qdrant/memory_reads.py`,
`app/repositories/embeddings/embedding_adapter.py` and the builders of the tool documentation were read in part, where they touch tenants, ids, arguments, pagination or credentials. Line
numbers are those of the tree described above.

| Area | Where it was read |
|---|---|
| Startup and refusals | `_configuration_refusal` (`main.py:441`), `_serve` (`main.py:543`), `_run_server` (`main.py:515`), `ensure_startup_allowed` (`app/config/auth.py:178`) |
| HTTP middleware stack | `_http_middleware` (`main.py:391`), `SecurityHeadersMiddleware` (`app/middleware/security_headers.py:49`), `LoopbackGuardMiddleware` (`app/middleware/loopback_guard.py:100`), CORS in `_http_middleware`, `BodyLimitMiddleware` (`app/middleware/body_limit.py:32`) |
| REST routes (9 mapped, 3 public) | `ROUTE_TOOLS` (`app/routes/api/access.py:36`), `authorize_request` (`app/routes/api/access.py:49`), `read_json_object` (`app/routes/api/request_utils.py:81`), `validation_error_response` (`app/routes/api/validation.py:12`), `health_check` (`app/routes/api/health.py:24`), `get_auth_info` (`app/routes/api/auth.py:28`), the pagination of the list route (`app/routes/api/memories.py:80`) |
| MCP (3 meta-tools, 10 tools) | `execute_axmem_tool` (`app/routes/mcp/meta_tools.py:431`), `reject_invalid_argument_names` (`app/routes/mcp/meta_tools.py:299`), `ensure_tool_executable` (`app/routes/mcp/meta_tools.py:317`), `ToolRegistry.execute` (`app/routes/mcp/tool_registry.py:187`), `client_error_detail` (`app/routes/mcp/tool_errors.py:34`), the adapters in `app/routes/mcp/adapters/` |
| Authentication and tenant | `tenant_from_claims` (`app/middleware/auth.py:167`), `TokenCache` (`app/middleware/auth.py:58`), `get_identity_from_request` (`app/middleware/auth.py:280`), `StaticTokenVerifier` (`app/middleware/static_token.py:22`), `ExpiringJWTVerifier` (`app/middleware/token_verifiers.py:29`), `_build_jwt` (`app/config/auth.py:74`) |
| Scopes | `effective_permitted_tools` (`app/routes/mcp/scope_resolver.py:168`), `token_scopes_from_claims` (`app/routes/mcp/scope_resolver.py:268`) |
| Tenant filter and Qdrant | `scoped` (`app/repositories/qdrant/store.py:124`), `Scope` (`app/repositories/qdrant/store.py:50`), `Tenant.id` (`app/models/tenant_models.py:21`), the search and read paths in `app/repositories/qdrant/memory_search.py` and `app/repositories/qdrant/memory_reads.py`, the link creation in `app/repositories/qdrant/memory_links.py` |
| Backup, restore, export, import | `create_backup` (`app/repositories/qdrant/backup.py:71`), `restore_backup` (`app/repositories/qdrant/backup.py:145`), `_check_entry` (`app/repositories/qdrant/backup.py:231`), `export_memories` (`app/repositories/qdrant/archive.py:90`), `import_memories` (`app/repositories/qdrant/archive.py:159`), `open_new_private_text_file` (`app/utils/private_files.py:55`) |
| Outbound connections | `_build_client` (`app/repositories/qdrant/client.py:103`), `GatewayEmbeddingsAdapter._endpoint` (`app/repositories/embeddings/gateway_adapter.py:162`), `HttpRerankAdapter` (`app/repositories/embeddings/reranker_adapter.py:20`), `app/repositories/embeddings/embedding_adapter.py` (the four SDK providers), the JWKS and introspection verifiers of FastMCP |
| Models and files on disk | `fetch_model` (`app/repositories/embeddings/tensorrt_model.py:140`), `_verify_or_record` (`app/repositories/embeddings/tensorrt_model.py:125`), `_cached_engine` (`app/repositories/embeddings/tensorrt_cache.py:134`) |
| Logging | `SensitiveDataFilter` (`app/config/logging_config.py:91`), and every `logger.*` call of `app/` that names a request, a token, a key, a URL or a path (a search, see "Checked, no finding") |
| CLI | `normalize_server_url` (`app/routes/cli/remote_executor.py:44`), `login` (`app/routes/cli/auth_commands.py:105`), `_build_executor` (`app/routes/cli/parser.py:248`), `run_operator_command` (`app/routes/cli/operator_commands.py:158`), `sanitize_line` (`app/routes/cli/render.py:49`) |
| Settings and TLS | `Settings` (`app/config/settings.py:121`) (every field and validator), `ensure_tls_usable` (`app/config/tls.py:89`) |
| Pipeline | the seven files of `.github/workflows/`: triggers, permissions, and every `${{ }}` expression |

### Tools run

All on 2026-10-29, on the tree above, from the repository root.

| Tool | Version | What was run | Result |
|---|---|---|---|
| Bandit | 1.9.4 (Python 3.14.7) | The gate's command and `pyproject.toml` configuration over `app main.py test_harness scripts fuzz` (tests excluded by the configuration) | No findings. Three lines carry a documented `# nosec`; with `--ignore-nosec` over `app` and `main.py` only those three are reported: `B104` for the `0.0.0.0` default of `SERVER_HOST` and two `B105` false positives, for the field name `op_token` and the scope `*` |
| Semgrep | 1.181.0, the pin of the `semgrep` gate, run with `uvx` | The local rules of `.semgrep/axmem.yml` plus `p/python` and `p/security-audit`, over `app main.py test_harness scripts fuzz`, with `--no-git-ignore` so that the files not yet committed are read | 214 rules on 526 files, no findings. One parse error: `scripts/release/facts.py` (finding SR-12) |
| Trivy | 0.75.0, image `aquasec/trivy:0.75.0` (digest `sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa`); vulnerability database built 2026-10-29 13:09 UTC | `fs --config trivy.yaml --ignorefile .trivyignore .`: HIGH and CRITICAL, fixed vulnerabilities only, plus secrets and misconfigurations | 0 vulnerabilities (`uv.lock`, `test_harness/docker/package-lock.json`), 0 misconfigurations (the harness `Dockerfile`), 0 secrets (the scan includes a local settings file of the working directory, whose content was not read) |
| OSV-Scanner | 2.6.0 (osv-scalibr 0.5.2), image `ghcr.io/google/osv-scanner@sha256:afd838850ac1a0fcc15ff4a041dc9ba11123c3f0d2666217a5f0fcf9222b55fa` | `scan source -L uv.lock` against the live OSV database. The older binary at `/usr/local/bin/osv-scanner` (2.0.3) was not used | 164 packages, "No issues found". Unlike Trivy's gate, OSV also reports vulnerabilities that have no fix yet |
| Atheris | 3.1.0 | The three harnesses of `fuzz/`, `-max_total_time=60` each, run one after the other, without a corpus, crash files directed away from the repository | No crash. 4,241,626 inputs (`fuzz/fuzz_token_claims.py`), 22,865,570 (`fuzz/fuzz_request_arguments.py`), 5,794,850 (`fuzz/fuzz_terminal_text.py`) |
| Architecture tests | pytest | `uv run --frozen --no-sync pytest tests/architecture/` (the import boundaries, the workflows, the release scripts) | 399 passed |

Every command used `uv run --frozen --no-sync` where it ran project code.

### Probes written for this review

* **Regular-expression timing** on adversarial text: the 6 patterns of the log filter at its 32,768-character cap (the worst
  took 66 ms, a text of repeated `://a:`), and the free-text URL scrubber of the CLI (finding SR-4: 52 ms for 10,000
  characters, 211 ms for 20,000, 835 ms for 40,000).
* **The settings file of the working directory**: a throwaway directory holding a settings file that names a server, a throwaway
  user-config directory, and a child process that loads the settings (finding SR-1: the setting took the value; since the fix it does not).
* **A search** of `app/` and `main.py` for `subprocess`, `os.system`, `eval`, `exec`, `pickle`, `yaml`, `marshal`, `np.load`,
  `torch.load`, `tarfile`, `zipfile`, `extractall`, `shutil.unpack_archive`, for every HTTP client and every `open(`, and for
  every `logger.*` call that names a request or a secret.

### Not done

* The test suites (integration, end-to-end, cluster), coverage, mutation and memory budgets were not run by this review. They
  are the maintainer's gates ([Quality gates](quality-gates.md)).
* No dynamic scanner or hand-made attack was run against a live server over HTTP, apart from what the existing end-to-end
  suite does. No slow-request or connection-exhaustion test. No fuzzing of the HTTP surface or of the request models.
* The release scripts (`scripts/release/`) and the test harness were run through Bandit, Semgrep and Trivy and not read.
* No reading of the code of the dependencies, no test of a hostile Qdrant, identity provider or embeddings server.
* No review of the GitHub settings beyond what [docs/security.md](security.md#supply-chain) already records.

## The security boundary and its trust assumptions

The boundary is where bytes cross from something Axmem does not control to something it does. Seven crossings exist; the
assurance case draws them in a [diagram](assurance-case.md#trust-boundaries-and-entry-points).

| Crossing | Trusted side | Untrusted side | What decides trust |
|---|---|---|---|
| HTTP listener (REST, `/mcp`) | The code after the credential and scope checks | Every HTTP client, an authenticated one included (a tenant is untrusted with respect to the other tenants), every header, path, query and body | The credential mode, the scope ceiling, the tenant filter, the body limit |
| stdio | The process that started the server | Nothing remote: stdio carries no credential, so only `AUTH_MODE=none` is allowed on it | The operating system |
| Tool arguments (`execute_axmem_tool`) | The adapters, once arguments are coerced | Tool name, argument names and values, which come from an agent that may have read hostile text | `reject_invalid_argument_names`, the coercions, the pydantic models |
| Qdrant | Qdrant and everybody who holds its API key | Nothing: Qdrant is trusted with every tenant's data, and its answers are not validated beyond their shape | The key, the network |
| Embeddings, rerank and identity providers | The operator's choice of them | What they answer: vectors (checked for width and finite numbers), rerank results (not checked, SR-3), JWKS and introspection answers (FastMCP's code) | The URL the operator configured |
| Files: backups, exports, models, engine cache, TLS key | The operator, and the account that runs Axmem | The *content* of a file someone else made (an export, a backup, a model) | File permissions; checksums that detect damage and not tampering (SR-7, SR-13) |
| Release pipeline | The maintainer's machine and the pinned workflows | Dependencies, Hugging Face, the network | Pins, hashes, signing ([docs/security.md](security.md#supply-chain)) |

**Trusted without checking:** the operator (settings, the command line, operator commands), Qdrant and its key holders, the
GPU host (operating system, driver, Python, TensorRT), the identity provider of `jwt` and `introspection`, and the
embeddings or rerank service the operator names. The settings file of the working directory is read as the operator's, with
one exception: it cannot name the server the CLI sends `AXMEM_TOKEN` to (SR-1). It can still name the Qdrant node and the
provider URLs of a local-mode CLI (SR-16).

**Untrusted:** HTTP and MCP clients and everything they send, tool arguments, text read from a file made by someone else, and
what a provider answers. An imported export is *parsed* as untrusted (each line is validated and a bad line is skipped), but
its tenant ids are honoured, because `import` is an operator command that works across tenants.

## Review of the requirements

The question for a requirement is not only "is it enforced" (the assurance case argues that) but "is it the right one, and is
anything missing". Reading [docs/security.md](security.md) against the code:

* **AUTH-1 to AUTH-7, SCOPE-1, SCOPE-2, TEN-1, TEN-3, NET-1, NET-2, NET-4, NET-5 and SEC-1 to SEC-5** were checked against the
  code they cite and say what the code does. TEN-2 and NET-3 were checked through the filter, the request helpers and their
  tests (the request models were not read field by field). NET-6 was checked only as far as the list of outbound connections
  goes, and the supply-chain requirements (SUP-1 to SUP-8) were not re-verified: this review read the workflows, not the
  release scripts. Two statements were corrected in this change because the code differs: four credentials were listed as
  plain strings in the settings and there are five (SR-5), and the limit of the loopback guard that every loopback origin, on
  any port, is let through was not among the stated non-guarantees (SR-8).
* **NET-7 to NET-11 and SEC-6** (the TLS option, the plain-HTTP warning, the security headers) are new in this change and
  were read with the names and the main bodies of their tests. They hold, with the limits written into them: TLS stays off by default, the headers are
  absent from a `500`, HSTS is sent only when this process serves TLS.
* **Requirements that were missing**, now in the findings: nothing says how a *credential in transit to a service Axmem
  calls* is protected, apart from the Qdrant key (SR-2); nothing says what the *CLI* trusts, only that it does not store a
  token (SEC-3), while it took the server it sends the token to from a file in the working directory (SR-1, now SEC-7); nothing requires
  the *answers of a provider* to be validated, and only vectors are (SR-3); nothing covers availability beyond the size of
  a body (SR-11).
* **Requirements that are weaker than they read**: NET-5 promises a generic message for an unexpected failure, and
  the code decides what is "expected" by exception type, so every `ValueError` and `TypeError` reaches the client as it is
  (SR-10). SCOPE-1 says a token can only narrow the ceiling; true on the supported path, and the code has a fallback that
  would not (SR-6).

## Findings

Severity is a judgement, not a score. **Medium**: a plausible attacker that the operator does not control reaches a secret
or another party's data. **Low**: needs a misconfiguration, or affects only robustness, or one process. **Info**: a limit
or a hardening idea. Status: **open** (a code change is recommended and not made), **accepted** (a limit that is documented,
no change proposed), **fixed in tree** (changed in the uncommitted tree). The review itself changed no code; SR-1 was fixed
afterwards, at the maintainer's request, in the same tree, and a check of that fix added SR-16.

In all, 16 findings: 1 Medium, 9 Low and 6 Info; 3 fixed in the tree (SR-1, SR-14, SR-15), 6 accepted (SR-7, SR-8, SR-9, SR-11,
SR-13, SR-16) and 7 open (SR-2 to SR-6, SR-10, SR-12).

| ID | Severity | Status | Finding | Evidence |
|---|---|---|---|---|
| SR-1 | Medium | fixed in tree | A settings file in the working directory could name the server the CLI sends `AXMEM_TOKEN` and every memory to. The CLI now ignores `AXMEM_SERVER` there and says so | `app/config/settings.py:110`, `app/config/settings.py:51`, `app/config/settings.py:483`, `app/routes/cli/parser.py:270`, `tests/integration/test_cli_server_origin.py:466` |
| SR-2 | Low | open | Credentials can travel over `http://` to a remote service with no warning (only the Qdrant key is warned about) | `app/config/settings.py:190`, `app/config/settings.py:197`, `app/repositories/embeddings/reranker_adapter.py:52`, `app/repositories/embeddings/gateway_adapter.py:167`, `app/repositories/qdrant/client.py:116` |
| SR-3 | Low | open | The answer of an HTTP rerank service is used without checking its indexes | `app/repositories/embeddings/reranker_adapter.py:71`, `app/repositories/qdrant/memory_search.py:102` |
| SR-4 | Low | open | The CLI's scrubber for URLs in free text takes quadratic time on a long run of letters | `app/routes/cli/render.py:19`, `app/routes/cli/auth_commands.py:129`, `app/routes/cli/auth_commands.py:165` |
| SR-5 | Low | open (docs corrected) | The introspection client secret is a plain string, and the log filter has no pattern for `secret=` | `app/config/settings.py:199`, `app/config/logging_config.py:105` |
| SR-6 | Low | open | The scope resolver falls back to every tool when the instance ceiling is empty, and a failed token lookup drops the token's scopes | `app/routes/mcp/scope_resolver.py:191`, `app/routes/mcp/scope_resolver.py:231` |
| SR-7 | Low | accepted (docs extended) | Model files and TensorRT engines are checked against damage, not against someone who can write their directory | `app/repositories/embeddings/tensorrt_cache.py:148`, `app/repositories/embeddings/tensorrt_cache.py:152`, `app/repositories/embeddings/tensorrt_model.py:125` |
| SR-8 | Info | accepted (docs added) | A server with `AUTH_MODE=none` answers a page on any loopback origin, any port | `app/middleware/loopback_guard.py:92` |
| SR-9 | Info | accepted | `/health` shows the text of a model's failure to a caller with no credential | `app/routes/api/health.py:53`, `app/repositories/embeddings/tensorrt_runtime.py:505` |
| SR-10 | Info | open | An MCP failure is shown as it is for every `ValueError` and `TypeError`, whatever raised it | `app/routes/mcp/tool_errors.py:24` |
| SR-11 | Info | accepted | No limit on how slowly a request arrives, or on connections; not tested | `main.py:535`, `app/middleware/body_limit.py:32` |
| SR-12 | Info | open | Semgrep cannot parse one release script, so the gate skips it silently | `scripts/release/facts.py:356` |
| SR-13 | Info | accepted (docs added) | A backup or an export is trusted: checksums detect damage, and an export decides the tenant of each memory | `app/repositories/qdrant/backup.py:170`, `app/repositories/qdrant/archive.py:236` |
| SR-14 | Low | fixed in tree | The server could not speak TLS (earlier a documented non-guarantee). It can now, opt-in, and warns when it does not | `app/config/tls.py:89`, `app/config/tls.py:121`, `main.py:563` |
| SR-15 | Low | fixed in tree | Answers carried no security headers (earlier a documented non-guarantee) | `app/middleware/security_headers.py:38`, `main.py:411` |
| SR-16 | Low | accepted | The settings file of the working directory still names the Qdrant node and the provider URLs of a local-mode CLI, so a hostile file in a cloned repository can send the Qdrant key from the environment, and the memories, to a host of its own. `AXMEM_TOKEN` is not sent. The loader is left as it is because that file is the documented way to configure the server and the local mode | `app/config/settings.py:110`, `app/config/settings.py:156`, `app/config/settings.py:158`, `app/routes/cli/parser.py:274`, `tests/integration/test_cli_server_origin.py:267` |

None of the findings, as far as this review could tell, lets a caller reach another tenant's data, skip authentication, or run
code on the server. That is a statement about what was read, not a proof.

### SR-1: the working directory decided which server the CLI trusts (Medium, fixed in tree)

Before the fix, settings were read from a file in the working directory and from the user's settings file; the user's file won,
and a real environment variable beat both. `AXMEM_SERVER` switches the CLI to remote
mode, and `AXMEM_TOKEN`, read from the environment only, is the bearer token it sends there
(`app/routes/cli/parser.py:268`). So a settings file that
arrived with a cloned repository, in the directory where a person or an agent runs `axmem`, could point the CLI at a server of its
own.

*Reproduced* (before the fix): a throwaway directory with a settings file that sets `AXMEM_SERVER` to a made-up host, a throwaway
user-config directory, and a child process in that directory that loaded the settings. `settings.AXMEM_SERVER` had the made-up value.

*Preconditions*: `AXMEM_SERVER` is set neither in the environment nor in the user's settings file (`axmem auth login` writes it
there, and it then won), and the person has `AXMEM_TOKEN` exported. *Impact*: that server receives the bearer token and every
memory the CLI saves or searches, and it answers with text an agent will read. The same file also configures `axmem serve` run
in that directory (`AUTH_MODE`, `QDRANT_URL`, the provider URLs); what that leaves open for a local-mode CLI is SR-16.

*Fix* (in the tree, at the maintainer's request): the settings are read through a source that drops `AXMEM_SERVER` from the file
of the working directory after it is parsed (`app/config/settings.py:110`), so `export`, quotes, any
case and `${}` expansion do not matter, and a symlinked file is filtered like any other. The CLI takes the server from `--server`,
the environment and the user's file only. When the file of the working directory names a server and none of those does, the CLI
runs in local mode and prints one line on stderr (`app/config/settings.py:483`), without the ignored
value. Every other setting of that file, and the file as a whole for `axmem serve`, is read as before (see SR-16). *Proved by*
`tests/integration/test_cli_server_origin.py`, which includes the reproduction as a regression test and a listener on `127.0.0.1`
that receives no connection and no token (and, as the control, does receive them when the person names the server in the
environment). *Consequence for users*: whoever relied on a settings file in the working directory to choose the server must
move `AXMEM_SERVER` to the environment, to `--server` or to the user's file (`axmem auth login`). *Not covered*: `QDRANT_URL` and
the provider URLs in that file still steer a local-mode CLI, as they steer `axmem serve`. `AXMEM_TOKEN` is not sent to those
hosts, but the Qdrant key, the provider keys and the memories can be; that is SR-16, which is accepted.

### SR-2: plain `http://` to a remote service raises no warning (Low, open)

`QDRANT_URL` has a validator and `_build_client` warns when the Qdrant key would cross a network in clear text. Nothing
alike exists for the other places a credential or a trust decision depends on the scheme: `FASTMCP_SERVER_AUTH_JWT_JWKS_URI`
and `FASTMCP_SERVER_AUTH_INTROSPECTION_URL` are passed through unchecked (`app/config/auth.py:77`),
the gateway accepts `http` (`app/repositories/embeddings/gateway_adapter.py:167`), the HTTP rerank service
needs only a non-empty `RERANKING_URL`, and the CLI keeps an explicit `http://` server (`app/routes/cli/remote_executor.py:58`).
Over `http://`, the introspection client secret, the rerank key, the gateway token and `AXMEM_TOKEN` cross the network in clear
text, and a key set fetched over `http://` can be replaced by whoever sits on the path, which would let them mint tokens the
server accepts. Each of these is the operator's own choice of URL, hence Low.

*Suggested fix*: refuse, or at least warn at start, for a non-loopback `http://` JWKS URI, introspection URL, rerank URL and
gateway URL, as `_build_client` does for Qdrant; warn in the CLI when it is about to send a token over `http://` to a non-loopback
host. FastMCP's JWT verifier also has an `ssrf_safe` option that fetches the key set with a size cap of 64 KiB through its own
hardened transport (`.venv/lib/python3.14/site-packages/fastmcp/server/auth/providers/jwt.py:462`); Axmem does not use it, and the plain fetch has no size cap. The URI is the operator's, so this is optional.

### SR-3: rerank answers are not validated (Low, open)

The gateway adapter checks the count, the width and the finiteness of what it gets (`app/repositories/embeddings/gateway_adapter.py:91`), and the base
class of the SDK providers checks width and finiteness (`app/repositories/embeddings/embedding_adapter.py:56`). The HTTP rerank adapter returns
`(r["index"], float(r["relevance_score"]))` for whatever the service sent, and `_rerank` indexes `candidates[index]`
with it. No tenant boundary is crossed: the candidates were already filtered by tenant before they were sent to the service. A
faulty or hostile rerank service can still make a search fail (an index out of range raises `IndexError`: the generic internal error over MCP,
a `500` over REST), return a candidate twice, or, with a negative index, choose another candidate of the same tenant.
*Suggested fix*: check that each index is an integer in `range(len(documents))`, that indexes are distinct and that scores are
finite, and cap the size of the answer.

### SR-4: `redact_urls_in_text` is quadratic (Low, open)

`app/routes/cli/render.py:19` is unanchored and starts with `[A-Za-z][A-Za-z0-9+.\-]*`, so on a long
run of letters and digits with no `://` the engine retries from every position and reads to the end of the run each time.
Measured: 52 ms for 10,000 characters, 211 ms for 20,000, 835 ms for 40,000 (about four times for each doubling). It is used
on `str(exc)` by `axmem auth login` and `axmem auth status`, so the text can come from a server the CLI is talking to; a
hostile server could stall that one command. The Atheris harness feeds it at most a few kilobytes and cannot find it.
*Suggested fix*: bound the length of the scheme (`{0,31}`), or cut the text before scanning, as the log filter does.

The log filter itself was measured at its cap of 32,768 characters with nine adversarial texts; the slowest took 66 ms.

### SR-5: a credential that the log filter cannot recognise (Low, open)

`FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET` is a plain `str`, and `SENSITIVE_PATTERNS` masks `password`, `token`, `api-key`
and `Bearer` values and credentials in URLs, not `secret=`. No code path logs the settings or this value today (searched: the
settings are never dumped, and the verifier is FastMCP's), so nothing leaks now; the next log line that does would not be masked.
FastMCP's `IntrospectionTokenVerifier` accepts a `SecretStr` for the secret
(`.venv/lib/python3.14/site-packages/fastmcp/server/auth/providers/introspection.py:85`). *Suggested fix*: make the
four provider keys and this secret `SecretStr`, and add a `secret` pattern to the filter. [security.md](security.md#what-axmem-does-not-guarantee)
now lists the five.

### SR-6: the scope resolver can fail open (Low, open)

`effective_permitted_tools` returns every tool, with scope `*`, when the instance ceiling is empty or was never set
(`app/routes/mcp/scope_resolver.py:191`; the comment says "backwards compatible"), and `_current_access_token`
turns any exception into "no token", which means "no scope claim", which means "the full ceiling" (`app/routes/mcp/scope_resolver.py:231`).
Neither is reachable on the supported path: `lifespan` sets the ceiling before the server reports ready (`main.py:120`),
any valid `AXMEM_SCOPES` permits at least one tool, and the tenant lookup that follows would refuse a request without a token.
It is a gap in depth, in the one function every scope decision goes through. *Suggested fix*: an empty ceiling denies every tool, and a failed
token lookup denies the request.

### SR-7: model and engine files (Low, accepted)

The first fetch of a model records the SHA-256 of its files in `manifest.json` in the same directory, and a later start refuses
files that differ (`app/repositories/embeddings/tensorrt_model.py:125`). A cached TensorRT engine is checked
only by the fingerprint file that names its directory, and then handed to TensorRT to deserialise
(`app/repositories/embeddings/tensorrt_cache.py:148`); there is no digest of the plan. A model
that is not in `PINNED_REVISIONS` is fetched at the default branch. Whoever can write the model directories or the engine cache
can change what the embedder runs, in native code. That is the account that runs Axmem, and assumption H1 of the
[assurance case](assurance-case.md#assumptions), so it is accepted; [security.md](security.md#operating-axmem-securely) now
names the engine cache directories next to the model directories. *Optional hardening*: record a digest of each plan and check it
before deserialising.

### SR-8 to SR-13 in short

* **SR-8** The loopback guard lets through any `Origin` that names `localhost` or a loopback address, on any port
  (`app/middleware/loopback_guard.py:92`). Another application on the machine,
  or a page served from `localhost:<other port>`, can use a server with `AUTH_MODE=none`, as a local process can. This is what the
  mode means; it is now written in [security.md](security.md#what-axmem-does-not-guarantee).
* **SR-9** On a failed model, `/health` answers 503 with the exception text of the failure to anyone
  (`app/routes/api/health.py:53`). It is documented, and the text is a CUDA or TensorRT message, but it is
  detail about the host handed to an unauthenticated caller. The probe needs only the status code: log the text, return a
  fixed sentence.
* **SR-10** `client_error_detail` shows `str(exc)` for `ValueError` and `TypeError` (`app/routes/mcp/tool_errors.py:24`).
  Pydantic's `ValidationError` is a `ValueError`, and that is the intended case, but any library that raises a `ValueError` with
  a URL or an id in it is shown too. No such case was found in the code that was read; the tests cover the current tools. An
  allow-list by type, not by origin, is as safe as every raiser.
* **SR-11** `_run_server` gives uvicorn only `timeout_graceful_shutdown` and the TLS options. The body limit counts bytes, not time,
  and there is no connection limit; security.md already lists "no rate limiting". A slow client was not tested, and uvicorn's own
  handling of one was not read. The reverse proxy is expected to apply these limits (H3).
* **SR-12** Semgrep 1.181.0 reports a syntax error for the generic function syntax of Python 3.12 in `scripts/release/facts.py`
  (`scripts/release/facts.py:356`). The `semgrep` gate passes, so the file, a release script, is read by Bandit, Ruff, mypy
  and pylint but not by Semgrep. Newer Semgrep releases may parse it; the pin of the gate is `SEMGREP_VERSION` in
  `scripts/quality/static.py`.
* **SR-13** `restore` checks the manifest as untrusted (file names, collection names, node order, checksums), and still the
  checksums are in the same directory as the files they cover. `import` validates every line, never overwrites an existing id, and
  takes the tenant of each memory from the file. Both are operator commands and are written into
  [security.md](security.md#what-axmem-does-not-guarantee) as trusted inputs.

### SR-14 and SR-15: fixed in the tree

Both were non-guarantees of the previous version of [docs/security.md](security.md): "no TLS in the server" and "no HTTP security
headers". They are now requirements NET-7 to NET-11 and SEC-6, with tests that start a real server over a real certificate chain
(`tests/e2e/test_tls_serve_e2e.py`) and that read every refusal of the middleware for the headers
(`tests/integration/test_security_headers.py`). The TLS option is opt-in and changes no default: plain HTTP
is still what a server speaks until the operator turns TLS on, and a half-set TLS configuration refuses to start instead of
falling back. The headers are always on and have no setting. The review read `app/config/tls.py:57`, `app/config/tls.py:75` and
`app/middleware/security_headers.py:57` and found no defect in them.

### SR-16: the settings file of the working directory still names the other destinations (Low, accepted)

Found when a reviewer checked the fix of SR-1. `_TrustedDotEnvSource` drops one name, `AXMEM_SERVER`
(`app/config/settings.py:51`), and reads the rest of the file of the working directory as
before, because that file is the documented way to configure `axmem serve` and the local mode. In local mode the CLI runs the
services in its own process (`app/routes/cli/parser.py:274`), so `QDRANT_URL`
(`app/config/settings.py:156`), `GATEWAY_URL`, `RERANKING_URL`, `OPENAI_BASE_URL`, `AZURE_ENDPOINT` and `OLLAMA_BASE_URL`
from that file decide which hosts the process connects to. A real environment variable beats the file, so a `QDRANT_API_KEY`
(`app/config/settings.py:158`) or a provider key that the person has in the environment or in their own
user settings file is used with those hosts.

*Reproduced* by a reviewer after the SR-1 fix: a settings file that sets `QDRANT_URL` to a host of its own, and `axmem memory recent` run
in that directory, connected there and sent the `QDRANT_API_KEY` read from the environment (a reviewer confirmed it is sent as well when the key is only in the user settings file). The same holds for `backup`, `export`, `import`,
`restore` and `qdrant-check`, which also run in local mode. *Test on record*: that the file's `QDRANT_URL` is read for local mode and for `serve`
is fixed by `tests/integration/test_cli_server_origin.py:267`; no test sends a key to a listener.

*Preconditions*: the person runs a local-mode command in a directory whose settings file they did not write, such as a freshly
cloned repository, and has the key in the environment or in their user settings file for the key to be sent. *Impact*: the host named in the file receives the Qdrant API
key, which admits every tenant of the real Qdrant node (the non-guarantee about the instance-wide key in
[security.md](security.md#what-axmem-does-not-guarantee)); the keys of the providers that file redirects, when those providers are in use; and, once it answers like a Qdrant
node, the memories the command saves, searches, backs up or exports. `AXMEM_TOKEN` is not sent in this case: only the remote
executor reads it. The existing warning of `_build_client` covers an `http://` node that is not on loopback
(`app/repositories/qdrant/client.py:116`) and says nothing for an `https://` one.

*Why accepted*: it is the same class of risk as running any tool that loads a settings file from the current directory (Docker
Compose and `python-dotenv` do), and the loader cannot be narrowed further without breaking the documented project settings file.
The maintainer's agent decided to leave the loader as it is after the fix of SR-1; the maintainer should confirm that. Severity is Low because it
needs a hostile file in a directory where the person chooses to run `axmem` in local mode, and it reaches the data of that person's own
instance, not another tenant's. *Advice*, written into [security.md](security.md#operating-axmem-securely): in a checkout you do not trust, run
`axmem` from another directory, and read the settings file of a cloned repository before running `axmem` there. Unsetting the keys in the shell
(for example `env -u QDRANT_API_KEY axmem ...`) helps only when they are not also in your user settings file, and it does not keep the memories
away from a host that answers, so another directory is the reliable choice.

## Checked, no finding

What was looked for, how, and what was found. A line here is a statement about this tree on this day.

| Hunt | What was done | Result |
|---|---|---|
| **SSRF** | Listed every outbound HTTP target and where its URL comes from; searched for any URL taken from a request. `source_url` is bounded to 2,048 characters and stored (`app/models/memory_models.py:266`), never fetched | Every target is a setting: `QDRANT_URL`, `GATEWAY_URL`, `RERANKING_URL`, the provider base URLs, the JWKS and introspection URLs, Hugging Face. No tool or route argument names a URL that is fetched. The httpx clients of the gateway and rerank adapters do not follow redirects (httpx 0.28.1 default, no override in `app/repositories/embeddings/gateway_adapter.py:218`). The redirect behaviour of the four provider SDKs was not checked. See SR-2 for the scheme |
| **Path traversal** | Searched `app/` and `main.py` for every `open(`, `Path(` and file operation | No route or tool takes a path. Paths come from the operator's command line. A backup manifest is read as untrusted: a file name must fully match `node<N>-<role>.snapshot` and equal the name derived from its node and role (`app/repositories/qdrant/backup.py:231`). A model repository id must match a strict pattern, so `..` cannot occur (`app/repositories/embeddings/tensorrt_model.py:44`). The snapshot name returned by Qdrant is placed in a URL path unescaped (`app/repositories/qdrant/backup.py:116`); Qdrant is trusted |
| **Unsafe deserialisation** | Searched for `pickle`, `yaml`, `marshal`, `shelve`, `np.load`, `torch.load`, `eval`, `exec`, `importlib`, `__import__` | None in `app/` or `main.py`. Only `json.loads` reads outside data, with `RecursionError` handled and `NaN`/`Infinity` refused in request bodies (`app/routes/api/request_utils.py:34`). The native parsers are TensorRT (plan, ONNX) and the tokenizers library: SR-7 |
| **Command injection** | Searched for `subprocess`, `os.system`, shell strings | No subprocess in `app/` or `main.py`; the scripts use argument lists |
| **Regular-expression DoS** | Listed all 20 regular expressions of `app/` and `main.py` (19 compiled, 1 built with `re.escape`); timed the 6 of the log filter on nine adversarial texts at its cap, and the scrubber of the CLI; read the other 13 | The patterns of the filter are bounded; the slowest text took 66 ms. The other 13 are anchored or made of single character classes. One quadratic pattern: SR-4 |
| **Timing-unsafe comparison** | Read the three token paths | `static` hashes the presented token and compares it with every configured hash by `hmac.compare_digest` (`app/middleware/static_token.py:39`); `jwt` and `introspection` are FastMCP's; the REST cache is keyed by a SHA-256 of the token |
| **Secrets in logs and errors** | Read every `logger.*` call that names a request, token, key, URL or path; searched for any dump of the settings; read the settings' error handling | Logs carry argument *names*, lengths, ids, host names and redacted URLs, never a body or a header (`app/routes/mcp/meta_tools.py:443`). Settings are never dumped; `hide_input_in_errors` is set and `describe_settings_failure` repeats no value. Static-token and TLS-password refusals are fixed text. Remaining: SR-5 |
| **CORS** | Read the validator and the middleware | Off by default; `CORS_ALLOW_CREDENTIALS` with `*` refuses to start (`app/config/settings.py:336`); the allowed request headers are four |
| **Request size and rate** | Read the body limit and the pagination | 4 MiB by default, declared and chunked; `limit` is 1 to 100 and an `offset` past the end returns at once (`app/repositories/qdrant/memory_reads.py:92`). No rate limit (documented); `rebuild_embeddings` lets a tenant with the write scope re-embed all its memories on the shared GPU, as repeatedly as it likes (availability, out of scope) |
| **Error leakage** | Read the REST and MCP error paths | REST errors are fixed sentences or the pydantic type, location and message without the input (`app/routes/api/validation.py:26`); an invalid id echoes at most 50 characters. MCP: SR-10. `/health`: SR-9 |
| **Authentication and authorisation bypass** | Read the startup refusals, `rest_route`, the meta-tool check, the three verifiers, and the test that enumerates the routes of the application | No route skips the credential or the scope check; the tenant comes from the verified claims only (`app/middleware/auth.py:167`); an incomplete mode refuses at start and again per request (`app/middleware/auth.py:183`). SR-6 for the one fallback |
| **Tenant isolation** | Read the one function that builds filters and the tenant model | Every filter is built by `scoped`, which puts the tenant condition first in `must`; a tenant id is a UUID derived from the name (`app/models/tenant_models.py:23`). The chokepoint test in `tests/integration/test_tenant_chokepoint.py` covers every repository method |
| **Private files** | Read the creation of backups, exports and the CLI's settings file | Directories `0700`, files `0600` created with `O_EXCL`; an export is written through a descriptor opened with `O_NOFOLLOW` after unlinking the name, so a planted link is not followed (`app/utils/private_files.py:66`). The CLI writes its server address to a temporary file in the same directory and renames it |
| **Workflows** | Searched the seven files for `pull_request_target`, `workflow_run`, expressions in `run:` and permissions | No `pull_request_target`, no `workflow_run`; `contents: read` at the top of every file; `id-token: write` only in the jobs that sign, publish or run Scorecard; the only expressions that carry user-influenced values (`github.ref_name`, `inputs.tag`) reach scripts through `env:`, not through the shell text |
| **Dependencies** | Trivy and OSV-Scanner on `uv.lock` | None known, on 2026-10-29. FastMCP (3.4.10), Starlette (1.3.1) and uvicorn (0.40.0) carry the HTTP surface and were not read (H6) |

## Conclusion

On the evidence above, Axmem's security requirements are specific, tied to code and tests, and in the parts that were read the
code does what they say. The tenant boundary rests on one function and on a test that checks every method that touches Qdrant;
authentication fails closed at start and again at request time; the new TLS and header code is small and well tested. The
review found no way into another tenant's data, past authentication, or to code execution. It found **one Medium**
(SR-1, the CLI trusting a settings file of the working directory for the server that receives its token; fixed in the tree
since, see SR-1), **nine Low**: hardening and consistency gaps (SR-2 to SR-7), two that are fixed in the tree (SR-14 and SR-15) and SR-16, which is
accepted: the same file still names the Qdrant node and the provider URLs of a local-mode CLI, and a few limits. Two earlier non-guarantees (no TLS, no headers) are now met by opt-in or fixed behaviour.
In all, 16 findings: 3 fixed in the tree, 6 accepted and 7 open.

The confidence is moderate. The author is an AI agent with no independent reviewer behind it; the dependencies were not read;
no attack was run against a live server; and the largest residual risks of the project are the ones already written in
[docs/security.md](security.md): plain HTTP by default, no rate limit, one maintainer, and Qdrant's key admitting every tenant.

## Open items for the maintainer

1. Read this page and adopt or redo it (see the top). Put the commit once the work is committed.
2. Find a second human reviewer; this page is a starting list for them.
3. Read the fix of SR-1, the only Medium, which is in the tree: it changes behaviour for anyone who set `AXMEM_SERVER` in a
   settings file of the working directory (see the changelog), so keep it or redo it.
4. Decide on SR-2 to SR-6, SR-10 and SR-12; the suggested fixes are written under each. Apart from SR-1, SR-14 and SR-15, nothing was changed in the code.
5. Confirm that SR-16 stays accepted (the loader of the settings file of the working directory is left as it is, and the advice
   is in security.md), or reopen it.
6. [SECURITY.md](../SECURITY.md) still says "no TLS in the server" in its list of what Axmem does not promise; that sentence is no
   longer true of the code (TLS is optional and off by default) and belongs to another file, so it was not edited here.
7. The changelog and [changes from Forgetful](changes-from-forgetful.md) should record this review and the corrected statements of
   security.md and the assurance case.

## When to review again

Proposed: before the final `0.6.0` is released, and then at least once a year (by 2027-10-29), and sooner when any of these
changes: an authentication mode, the tenant filter (`scoped`), a route or a tool, the TLS or header code, a new outbound
connection, a release workflow, or a major version of FastMCP, Starlette or uvicorn. The next review should be done by a
person who did not write the code, and should include the things this one did not: the dependencies' behaviour, a slow-request
test, and an attack run against a live server.
