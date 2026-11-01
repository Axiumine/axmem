# Security requirements

What an operator or a user of Axmem can expect from its security, and what they cannot. Every requirement names the code that
enforces it and the tests that prove it, so each can be checked. The argument that the requirements are met is in the
[assurance case](assurance-case.md); a review of the requirements and of the security boundary, with its findings, is in the
[security review](security-review.md); how to report a vulnerability is in [SECURITY.md](../SECURITY.md); how the project keeps,
uses and rotates its own credentials is in [Secrets and credentials](#secrets-and-credentials); the settings are in
the [configuration guide](configuration.md#authentication-configuration).

Checked against the source on 2026-10-29 (version `0.6.0rc1` plus the unreleased changes). Paths are relative to the
repository root. A requirement that has no test named is enforced by a tool listed in [Supply chain](#supply-chain).

An AI coding agent (Claude Code) working for the lead maintainer wrote this document and updates it when the code changes, and
the maintainer reviews the changes. No independent human has reviewed it; see
[who wrote the security review and what it is worth](security-review.md#read-this-first-who-wrote-this-and-what-it-is-worth).

## Threat model in short

Axmem is a memory server for AI agents. It holds text that people and agents wrote, so the assets are the **confidentiality
and integrity of each tenant's memories**, the **credentials** that guard them, the **availability** of the server and its GPU,
and the **integrity of the packages** users install.

| Who | Trusted? | What they can reach |
|---|---|---|
| A client of one tenant (an MCP client, the REST API, the CLI) | Only as far as its token and scopes | That tenant's memories, within its scopes |
| Another tenant | No | Nothing of this tenant |
| A network attacker with no credential | No | The three public routes and the refusals |
| An observer on the network path | No | Over plain HTTP, which is the default, everything the server sends and receives, tokens included. Over TLS (`SERVER_SSL_CERTFILE`, or a TLS-terminating proxy), only the size and timing of the traffic |
| A web page in the operator's browser | No | Nothing, against a server with a credential mode; refused by the loopback guard against `AUTH_MODE=none` |
| The operator | Yes | Everything: settings, operator commands (`backup`, `restore`, `export`, `import`, `re-embed`) |
| Qdrant and whoever holds its API key | Yes | Every tenant's data: the key is instance wide |
| The identity provider (`jwt`, `introspection`) | Yes | It decides who is which tenant |
| A dependency, a GitHub Action, a model file, the PyPI project | Verified, not trusted | See [Supply chain](#supply-chain) |

The trust boundaries are the HTTP listener (or the stdio pipe), the connection to Qdrant, the optional connections to
embeddings providers and identity providers, the files on disk (models, engine cache, backups) and the release pipeline.

## What Axmem guarantees

### Authentication and scopes

| ID | Requirement | Enforced by | Proved by |
|---|---|---|---|
| AUTH-1 | The server does not start unless `AUTH_MODE` is set to `none`, `jwt`, `introspection` or `static`. There is no default. | `ensure_startup_allowed` in `app/config/auth.py`, called first by `_serve` in `main.py` | `tests/integration/test_serve_fail_closed.py`, `tests/integration/test_auth_startup_contract.py` |
| AUTH-2 | `AUTH_MODE=none` is accepted only on stdio or on an HTTP bind to a loopback IP address: any address of `127.0.0.0/8` (not only `127.0.0.1`) or `::1`, as the `is_loopback` property of Python's `ipaddress` module defines it. A host name, `localhost` included, does not count. | `is_loopback_host`, `ensure_startup_allowed` in `app/config/auth.py` | the same tests |
| AUTH-3 | A credential mode is refused on stdio, which carries no bearer token. | `ensure_startup_allowed` | the same tests |
| AUTH-4 | A mode whose settings are incomplete never serves a request open: `serve` reports the cause and exits, and a request-level check refuses to answer as the default tenant unless the mode is `none`. | `AuthConfigError` in `app/config/auth.py`, `_local_tenant` in `app/middleware/auth.py` | `tests/e2e/test_incomplete_auth_mode_e2e.py`, `tests/integration/test_auth_factory.py` |
| AUTH-5 | `jwt` checks the signature, requires `exp`, and requires the issuer and audience to be configured and to match. `introspection` requires the endpoint to report the token active, and checks the audience when one is configured. `static` hashes the presented token with SHA-256 and compares it with every configured hash in constant time. | `ExpiringJWTVerifier`, `AudienceIntrospectionVerifier` in `app/middleware/token_verifiers.py`; `_build_jwt` in `app/config/auth.py`; `StaticTokenVerifier` in `app/middleware/static_token.py` | `tests/integration/test_token_verifiers.py`, `tests/integration/test_static_token_verifier.py`, `tests/e2e/test_auth_modes_e2e.py` |
| AUTH-6 | The tenant of a request comes from the verified credential only (the claim `tenant_id`, else `tenant`, else `sub`; the tenant a static token is configured for), never from a request argument. A claim that is not a string or integer, or is longer than 255 characters, is refused. | `tenant_from_claims`, `get_tenant_from_auth`, `get_identity_from_request` in `app/middleware/auth.py` | `tests/integration/test_auth.py`, `tests/e2e/test_api_auth.py` |
| AUTH-7 | The REST token cache keeps only a SHA-256 of the token, holds at most `TOKEN_CACHE_MAX_SIZE` entries, and never keeps an entry past `TOKEN_CACHE_TTL_SECONDS` or past the token's own expiry. | `TokenCache` in `app/middleware/auth.py` | `tests/integration/test_auth_token_cache.py`, `tests/e2e/test_auth_token_cache_e2e.py` |
| SCOPE-1 | A token can only narrow what the instance allows (`AXMEM_SCOPES`, default `*`), never widen it (see SR-6, an open finding of the [security review](security-review.md#findings)). A token that carries scopes but none that Axmem recognises is denied every tool. | `effective_permitted_tools`, `token_scopes_from_claims` in `app/routes/mcp/scope_resolver.py` | `tests/integration/test_token_scope_fail_open.py`, `tests/e2e/test_token_scope_fail_open_e2e.py`, `tests/integration/test_scope_resolver.py` |
| SCOPE-2 | Every memory operation is checked against the scopes, over MCP and over REST. Each REST route is registered through `rest_route` with the tool it stands for; a route with no entry in `ROUTE_TOOLS` cannot be registered, and a test fails for any route that is neither mapped nor public. | `ROUTE_TOOLS`, `authorize_request`, `rest_route` in `app/routes/api/access.py`; the check of `execute_axmem_tool` in `app/routes/mcp/meta_tools.py` | `tests/e2e/test_rest_scopes_e2e.py`, `tests/e2e/test_scoped_permissions_e2e.py`, `tests/e2e/test_rest_core_routes_require_credentials_e2e.py` |

A token with no `scope` claim gets the full `AXMEM_SCOPES` ceiling, and so does a static token, which carries none. Set
`AXMEM_SCOPES` to what the instance should allow at most.

### Tenant isolation

| ID | Requirement | Enforced by | Proved by |
|---|---|---|---|
| TEN-1 | Every repository method that takes a tenant (`user_id`) adds that tenant to the Qdrant filter of each read and write that touches memories or links, in one function. The methods that take no `user_id` (the generations, the count of all memories and the rebuild of links) are operator operations over every tenant, by design: the only service that calls them is the one behind the `re-embed` command (`re_embed_all`, reached from `main.py`), no route or tool does, and what they write carries a tenant on every point. | `scoped` in `app/repositories/qdrant/store.py`; the Semgrep rule `axmem-qdrant-write-outside-store` in `.semgrep/axmem.yml` keeps writes inside that module | `tests/integration/test_tenant_chokepoint.py` (every tenant method on a real node; the set of operator methods is derived from the signatures, a new method fails until it is classified; the points the operator methods write are checked for a tenant) |
| TEN-2 | A tenant cannot read, update, delete, link, unlink, search or list another tenant's memories, and another tenant's id answers like an id that does not exist. | the same filter, and the services above it | `tests/e2e/test_tenant_isolation_e2e.py` |
| TEN-3 | A link cannot join memories of two tenants, and the embedding used for a similarity search belongs to the caller. | `app/repositories/qdrant/memory_links.py`, `app/repositories/qdrant/memory_search.py` | `tests/e2e/test_memory_link_ownership_e2e.py`, `tests/e2e/test_cross_user_reference_checks_e2e.py` |

### Network exposure

| ID | Requirement | Enforced by | Proved by |
|---|---|---|---|
| NET-1 | A server running `AUTH_MODE=none` answers `421` to a `Host` that is not a loopback name (or one listed in `AXMEM_ALLOWED_HOSTS`) and `403` to an `Origin` that is neither loopback nor named in `CORS_ORIGINS`, on every route and on `/mcp`, so a web page cannot drive it by DNS rebinding or a cross-origin POST. | `LoopbackGuardMiddleware` in `app/middleware/loopback_guard.py`, installed by `_http_middleware` in `main.py` | `tests/integration/test_loopback_guard.py`, `tests/integration/test_loopback_guard_answers.py`, `tests/e2e/test_loopback_guard_e2e.py` |
| NET-2 | A request body above `REQUEST_BODY_MAX_BYTES` (4 MiB by default) is answered `413`: at once when the length is declared, and as soon as the count passes the limit for a chunked body. | `BodyLimitMiddleware` in `app/middleware/body_limit.py` | `tests/integration/test_body_limit.py`, `tests/integration/test_body_limit_hand_over.py`, `tests/e2e/test_request_bounds_e2e.py` |
| NET-3 | Every field that reaches the embedder or the store has a bound. Malformed input is a `4xx`, never a `500`: a body that is not JSON is `415`, a bad id `404`, a model that fails validation `400`. | the models in `app/models/`, `app/routes/api/request_utils.py`, `app/routes/api/validation.py` | `tests/e2e/test_request_bounds_e2e.py`, `tests/e2e/test_rest_malformed_requests_e2e.py`, `tests/integration/test_request_content_type.py`, `tests/integration/test_validation_error_response.py` |
| NET-4 | CORS is off by default. It never sends credentials to every origin: `CORS_ALLOW_CREDENTIALS` needs named origins, and the server refuses to start with it and `*`. | `Settings` validator in `app/config/settings.py`, `_http_middleware` in `main.py` | `tests/integration/test_cors_credentials.py` |
| NET-5 | An unexpected failure reaches an MCP client only as a short generic sentence; the cause is logged on the server. The exception is the text of a `ValueError` or a `TypeError`, which is shown as it is, whatever raised it (open finding SR-10 of the [security review](security-review.md#findings)). Argument names that are not identifiers are refused before they can reach a traceback. | `client_error_detail` in `app/routes/mcp/tool_errors.py`, `app/routes/mcp/meta_tools.py` | `tests/integration/test_mcp_meta_tools_hardening.py`, `tests/e2e/test_mcp_meta_tools_hardening_e2e.py` |
| NET-6 | With `AUTH_MODE=static`, the model files on disk and the default providers, the server contacts nothing but Qdrant. Remote embeddings and rerank providers, JWT key sets and introspection endpoints are contacted only when selected. | the provider selection in `app/bootstrap.py` | `tests/e2e/test_standalone_e2e.py` |
| NET-7 | With `SERVER_SSL_CERTFILE` and `SERVER_SSL_KEYFILE` the HTTP transport serves HTTPS. Both files or neither: the start ends with one `Error:` line and exit code 1, before anything is bound, and never falls back to plain HTTP, when only one of the two is set, when `SERVER_SSL_KEYFILE_PASSWORD` is set without them, when a file is missing or cannot be read, or when the two cannot be loaded as a certificate and a private key that belong together. Stdio serves no HTTP and ignores the settings. Nothing is served over TLS unless it is configured: plain HTTP stays the default. | `ensure_tls_usable` in `app/config/tls.py`, called by `_configuration_refusal` in `main.py`; `uvicorn_tls_options` in `app/config/tls.py`, called by `_run_server` in `main.py` | `tests/integration/test_tls_settings.py`, `tests/integration/test_tls_server.py`, `tests/e2e/test_tls_serve_e2e.py` (a real server in a child process, a real certificate authority and server certificate) |
| NET-8 | The TLS server offers TLS 1.2 or later (the minimum of the server context of Python 3.10 and later) and, for TLS 1.2, only ECDHE key exchange with AES-GCM or ChaCha20-Poly1305 (`TLS_CIPHERS`): forward secrecy and authenticated encryption. It asks for no client certificate; the bearer token is the credential. | `TLS_CIPHERS`, `uvicorn_tls_options` in `app/config/tls.py` | `tests/integration/test_tls_server.py` (the server context; a TLS 1.2 connection with a cipher outside the policy is refused; each cipher of the policy is accepted; TLS 1.3 by default) |
| NET-9 | A server that speaks plain HTTP on an address that is not loopback logs one `WARNING` at start that says tokens and memories cross the network in clear text and names both ways out (the TLS settings, or a loopback bind behind a TLS-terminating proxy). It does not refuse to start: `localhost` counts as loopback for this warning only, and `AUTH_MODE=none` still needs a loopback IP address (AUTH-2). | `plaintext_exposure_warning` in `app/config/tls.py`, logged by `_serve` in `main.py` | `tests/integration/test_tls_settings.py`, `tests/e2e/test_tls_serve_e2e.py` (a TLS server logs no such warning) |
| NET-10 | Every answer of the application on the HTTP transport carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` and `Cache-Control: no-store`, and `Cross-Origin-Resource-Policy: same-origin` unless `CORS_ENABLED`. The refusals of the other middleware carry them too (`421`, `403`, `413`, `401`, `404`, `405`), because the middleware is the outermost. A header the route set itself is kept (the MCP event stream keeps its own `Cache-Control`) and a streamed body is not buffered. A `500` from an unhandled exception is answered by Starlette outside every middleware and has none of them, and neither has the `400` that uvicorn itself sends for a malformed HTTP request, which never reaches the application. | `SecurityHeadersMiddleware` in `app/middleware/security_headers.py`, installed first by `_http_middleware` in `main.py` | `tests/integration/test_security_headers.py`, `tests/e2e/test_security_headers_e2e.py` |
| NET-11 | With `SERVER_SSL_CERTFILE` set, answers carry `Strict-Transport-Security: max-age=31536000` (no `includeSubDomains`, no `preload`), except to a request whose `Host` is `localhost` or a loopback address, because a browser pins a host name whatever the port. Without TLS in this process the header is never sent: behind a proxy it is the proxy's to send. | `SecurityHeadersMiddleware`, `names_loopback_host` in `app/middleware/loopback_guard.py` | `tests/integration/test_security_headers.py` (the `hsts` tests), `tests/integration/test_tls_server.py`, `tests/e2e/test_tls_serve_e2e.py` |

Three routes need no credential, because they read no memory: `/` (service information), `/health` (status, service name,
version, and the text of a failed model check) and `/api/v1/auth/info` (whether a credential is required, and which mode).

### Secrets

| ID | Requirement | Enforced by | Proved by |
|---|---|---|---|
| SEC-1 | Static tokens exist in the configuration only as SHA-256 hashes. The hash is all the resulting access token carries, so a token cannot reach a log line or an error message. A value that is not a 64-character hexadecimal digest is refused without being repeated. | `app/middleware/static_token.py`, the validator of `AUTH_STATIC_TOKEN_HASHES` in `app/config/settings.py` | `tests/integration/test_static_token_verifier.py`, `tests/integration/test_auth_settings.py` |
| SEC-2 | `QDRANT_API_KEY` and `GATEWAY_TOKEN` are secret values in the settings. A log filter masks passwords, tokens, `Authorization: Bearer` values, `api-key` values and the credentials in URLs in the message, its arguments, the traceback and the extra fields. | `SecretStr` in `app/config/settings.py`; `SensitiveDataFilter` in `app/config/logging_config.py`; `redact_url` in `app/utils/url_helpers.py` | `tests/integration/test_logging_config_contract.py`, `tests/integration/test_logging_masking_and_json_serialisation.py`, `tests/integration/test_url_redaction_replaces_one_userinfo.py` |
| SEC-3 | The CLI reads its bearer token from `AXMEM_TOKEN` and never writes it to a file; `axmem auth login` saves only the server address. | `app/routes/cli/auth_commands.py` | `tests/integration/test_cli_auth_status.py` (`test_login_sends_the_env_token_and_never_saves_it`) |
| SEC-4 | A backup or an export is created readable by its owner only (directories `0700`, files `0600`; an existing shared directory is left as it is). An export file is written without following a link planted in its place, and a failed backup leaves nothing behind. | `app/utils/private_files.py`, `app/repositories/qdrant/backup.py` | `tests/e2e/test_storage_backup_and_permissions_e2e.py` |
| SEC-5 | A Qdrant API key that would travel in clear text to a remote host (`http://` and not loopback) is reported as a warning at start. No other credential has this warning: see the [security review](security-review.md#findings). | `_build_client` in `app/repositories/qdrant/client.py` | `tests/integration/test_qdrant_client_contract.py` |
| SEC-6 | `SERVER_SSL_KEYFILE_PASSWORD` is a secret value in the settings. A refusal of the TLS settings never repeats it (the messages are fixed text), and the start logs no key material. | `SecretStr` in `app/config/settings.py`; `_configured`, `_load_pair` in `app/config/tls.py` | `tests/integration/test_tls_settings.py` (the refusals are compared with fixed text, also for a wrong and an over-long password), `tests/e2e/test_tls_serve_e2e.py` (`test_the_start_logs_no_plain_http_warning_and_no_secret`) |
| SEC-7 | In remote mode the CLI sends `AXMEM_TOKEN` and the memories it saves or searches only to the server the person named: `--server`, the `AXMEM_SERVER` environment variable or the user's settings file (`axmem auth login` writes it). The settings file of the working directory, which can arrive with a cloned repository, is never a source of `AXMEM_SERVER`, in any spelling of the line. When it names one and nothing else does, the CLI runs in local mode and prints one line on stderr, without the ignored value. Every other setting of that file is read as before. In local mode there is no server and no token is sent, and the CLI connects to the endpoints of the settings (`QDRANT_URL` and the provider URLs), which that file can name: the Qdrant key, the provider keys and the memories can reach the host it names ([security review](security-review.md#findings), SR-16, accepted; see the non-guarantee below). | `_TrustedDotEnvSource`, `Settings.settings_customise_sources`, `ignored_server_notice` in `app/config/settings.py`; `emit_ignored_server_notice` in `app/routes/cli/render.py`, called by `_build_executor` and `auth status` | `tests/integration/test_cli_server_origin.py` (the reproduction, the three sources, the spellings, a symlink, and a listener on `127.0.0.1` that receives no connection and no token) |

### Supply chain

| ID | Requirement | Enforced by | Proved by |
|---|---|---|---|
| SUP-1 | Every dependency in `pyproject.toml` (the runtime dependencies and the `dev` and `harness` groups) is pinned to an exact version with `==`, and so is every requirement of the build backend. `uv.lock` locks the resolved versions with hashes. CI and the hooks install with `--locked` or `--frozen`. | `pyproject.toml`, `uv.lock` | `tests/architecture/test_build_pins.py` (the build backend), `tests/integration/test_install_requirements.py` (the install commands against `pyproject.toml`, and the three TensorRT pins). No test requires the `==` of the other dependencies; the `--locked` install fails when `uv.lock` and `pyproject.toml` disagree |
| SUP-2 | Every GitHub Action is pinned to a 40-character commit, not to a tag that can move. The one exception, the SLSA generator, which its own verifier requires by tag, has its commit recorded and checked before each release. `.github/workflows/ci.yml` and `.github/workflows/quality.yml` hold no secret and have a read-only token. | `.github/workflows/`, `SLSA_GENERATOR_COMMIT` in `.github/workflows/release.yml` | `tests/architecture/test_workflows.py`, `tests/integration/test_release_guard_generator_tag.py` |
| SUP-3 | A release is built twice, on the maintainer's machine and in CI, and CI signs it only if its rebuild is byte-identical to the digests the first build recorded in `release-digests.json`. The files are signed with cosign (keyless, recorded in Rekor) and attested with SLSA provenance, and uploaded to PyPI by trusted publishing with an OIDC identity. No PyPI token or password exists anywhere in the flow. | `.github/workflows/release.yml`, `.github/workflows/publish.yml`, `scripts/release/guard.py`; see [Releasing Axmem](release.md) | `tests/architecture/test_release_workflow.py`, `tests/architecture/test_publish_workflow.py`, `tests/integration/test_release_guard_*.py`, `tests/integration/test_release_no_credentials.py` |
| SUP-4 | Static analysis and a vulnerability scan run before every push, and in hosted CI for the checks that need no GPU: `bandit`, Semgrep (local rules in `.semgrep/axmem.yml` plus `p/python` and `p/security-audit`), Ruff with the `S` (flake8-bandit) rules, `trivy` (fixed HIGH and CRITICAL vulnerabilities, secrets, misconfigurations), `import-linter`, `mypy`. | `scripts/quality/`, [Quality gates](quality-gates.md) | `tests/architecture/test_workflows.py` (the workflows run the gates by name), `tests/integration/test_pre_push_pipeline.py` (a failing or missing gate refuses the push) |
| SUP-5 | The wheel and the sdist hold Axmem's source and nothing else: no model weights, engines, NVIDIA libraries or credentials. | `scripts/quality/package.py` (the `package` gate) | `tests/integration/test_package_guard.py` |
| SUP-6 | The models the server loads are fetched at pinned Hugging Face commits for the default repositories, and the SHA-256 of their files is recorded on first use; a later start whose files differ refuses to run instead of building an engine from changed bytes. | `PINNED_REVISIONS`, the manifest in `app/repositories/embeddings/tensorrt_model.py` | `tests/integration/test_tensorrt_model.py`, `tests/integration/test_tensorrt_model_contract.py` |
| SUP-7 | Dependabot proposes version updates every week, as pull requests, for the GitHub Actions of `.github/workflows`, for `pyproject.toml` and `uv.lock`, and, for the test harness only, for the Docker base image and the npm packages in `test_harness/docker` (the project has no container deployment). The pull requests run `ci.yml` and `quality.yml` like any other change, and a maintainer merges them: no workflow merges one by itself. The three TensorRT packages and the SLSA generator are left out on purpose and are bumped by hand. | `.github/dependabot.yml` | No test: it is a setting of GitHub's Dependabot service, read from the default branch |
| SUP-8 | Two reports run on GitHub, outside the hooks: CodeQL analysis of the Python code and of the workflow files (`.github/workflows/codeql.yml`, on a push to `main` and on pull requests into it) and the OpenSSF Scorecard of the repository (`.github/workflows/scorecard.yml`, on a push to `main`). They are reports, not gates: no hook runs them, and an alert or a low score does not stop a push or a merge. A failed run of either on the release commit does stop a release until it is repeated. | `.github/workflows/codeql.yml`, `.github/workflows/scorecard.yml`, `scripts/release/ci_green.py` | `tests/architecture/test_workflows.py` (the shape of both workflows), `tests/integration/test_release_ci_green.py` |

Five settings of the GitHub repository are enabled as well: private vulnerability reporting (how to use it is in [SECURITY.md](../SECURITY.md)), secret scanning, push protection for secrets, and Dependabot alerts with Dependabot security updates. They are read from the GitHub API (2026-10-29) and are not files in the repository, so nothing here enforces them; they stay on only while the administrators of the repository keep them on.

## What Axmem does not guarantee

These are known limits. Several are the operator's job; none is hidden.

* **TLS is optional, and off by default.** Without `SERVER_SSL_CERTFILE` and `SERVER_SSL_KEYFILE` the HTTP listener speaks
  plain HTTP (NET-7), and a server reachable beyond one machine then sends tokens and memories in clear text. The server
  warns about it at start (NET-9) and does not refuse: it must be given the certificate (NET-7), or sit behind a
  TLS-terminating reverse proxy. The TLS option has no client certificates, no reload of a renewed certificate (restart the
  server) and no check of the permissions of the key file. `AUTH_MODE=none` is only safe while nothing forwards to its
  loopback port: a proxy, a tunnel or a port mapping makes every caller the default tenant. The server's connections to
  Qdrant, an identity provider or a remote provider use whatever scheme their URL names, and only a Qdrant API key sent
  over plain `http://` to a remote host is warned about (SEC-5); TLS certificates are verified by the HTTP libraries'
  defaults (the Semgrep rule `axmem-tls-verification-disabled` refuses `verify=False`).
* **No rate limiting, no lockout, no audit log.** The body limit and the field bounds cap the size of a request, not how many
  arrive. Put a limit in the proxy. The server logs some rejections (a token without `exp`, or issued for another audience, by client id and never
  the token) but keeps no record of which tenant read or changed which memory.
* **The security headers are fixed and not complete.** Every answer carries the headers of NET-10, and none is configurable.
  A `500` from an unhandled exception carries none of them. `Strict-Transport-Security` is sent only when this process
  serves TLS (NET-11); behind a proxy, set it there, with the proxy's own policy for `includeSubDomains` and `preload`. No
  `Permissions-Policy` or `Cross-Origin-Opener-Policy` is sent, because the server answers no page.
* **A credential-free server is open to every page on a loopback origin.** The loopback guard (NET-1) refuses a foreign
  `Host` and a foreign `Origin`, and lets through every origin that names `localhost` or a loopback address on any
  port. Another web application on the machine, or a page served from `localhost:<other port>`, can therefore use a server
  running `AUTH_MODE=none`, as a local process can.
* **A settings file in the working directory still steers a local-mode CLI.** The CLI does not take `AXMEM_SERVER` from it
  (SEC-7), so a cloned repository cannot choose the server that receives `AXMEM_TOKEN`; the finding that led to SEC-7 is fixed
  in the tree ([security review](security-review.md#findings), SR-1). Every other setting of that file is read, as it is for
  `axmem serve` run there, because that file is the documented way to configure the server and the local mode: `QDRANT_URL`
  and the provider URLs (`OPENAI_BASE_URL`, `AZURE_ENDPOINT`, `OLLAMA_BASE_URL`, `GATEWAY_URL`, `RERANKING_URL`) decide which
  hosts a local-mode command connects to. A hostile file in a cloned repository can therefore make `axmem memory recent`,
  `backup`, `export`, `import`, `restore` or `qdrant-check` connect to a host of its own and send it the `QDRANT_API_KEY`
  (and the provider keys) read from the environment or from your user settings file; once that host answers, it also receives the memories. `AXMEM_TOKEN` is
  not sent in this case. This is the risk of any tool that loads a settings file from the current directory (Docker Compose,
  `python-dotenv`); it is finding SR-16 of the [security review](security-review.md#findings), Low, accepted, and the loader is
  not narrowed further. In a checkout you do not trust, run `axmem` from another directory, and read the settings
  file of a cloned repository before running `axmem` there. Unsetting the keys in the shell (for example
  `env -u QDRANT_API_KEY axmem ...`) helps only when they are not also in your user settings file, and it keeps the keys away, not the memories.
* **No encryption of stored memories.** Memories and their vectors are plain payloads in Qdrant. Protect the disk, the
  Qdrant node and its snapshots (Axmem's own backups and exports are owner-readable only, but not encrypted).
* **A Qdrant API key admits every tenant.** Isolation between tenants is Axmem's job, enforced in `app/repositories/qdrant/store.py`;
  anyone who holds the key, or who can reach Qdrant, reads every tenant. All tenants share one process and one collection.
  Operator commands (`backup`, `restore`, `export`, `import`, `re-embed`) work across all tenants by design.
* **A static token does not expire.** Revoke it by removing its hash and restarting. Its hash is a plain SHA-256, which is
  fitting for a random token (the guide generates 32 random bytes) and not for a password someone chose. With `introspection`,
  revocation can take as long as `TOKEN_CACHE_TTL_SECONDS`; `TOKEN_CACHE_ENABLED=false` makes it immediate.
* **Not every credential is a secret value.** `AZURE_API_KEY`, `GOOGLE_AI_API_KEY`, `OPENAI_API_KEY`, `RERANKING_API_KEY`
  and `FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET` are plain strings in the settings. The log filter masks them where
  it recognises the pattern (`api-key=`, `password=`, `token=`, `Bearer`, a password in a URL), not by type, and it has no
  pattern for `secret=`. No code path logs the settings or these values today (the [security review](security-review.md#findings)
  looked).
* **A backup or an export file is trusted.** The SHA-256 digests in a backup manifest detect damage, not tampering: whoever
  can write the backup directory can change a snapshot and its digest, and `restore` then loads it into Qdrant. An export
  names the tenant of each memory, so `import` can add memories to any tenant (an existing id is never overwritten). Both
  are operator commands; keep the files where only the operator can write.
* **`jwt` does not check `nbf`, and `introspection` does not check the issuer.** The first is a limit of FastMCP's verifier,
  the second a choice: the introspection endpoint is the authority on whether a token is valid.
* **The content of a memory is not sanitised.** Axmem stores what it is given and returns it. A memory can contain
  instructions that a model reading it may follow (prompt injection). Treat recalled text as untrusted input in the client.
* **Remote providers see your text.** With a remote embeddings provider (Azure, Google, OpenAI, Ollama, the gateway), the
  title, content, context, keywords and tags of every memory go to it (`build_embedding_text` in
  `app/utils/embedding_helpers.py`), and so does every search query. A remote rerank service sees the query and the candidates.
  The default TensorRT provider keeps everything on the machine, apart from the first fetch of the model files from Hugging
  Face and of tiktoken's data.
* **The first fetch of a model is trusted on first use.** The revision is pinned, the hashes are recorded when the files are
  first seen, but the hashes themselves are not written in the source. A model that is not in
  `PINNED_REVISIONS` is fetched at its default branch, unpinned. The record (`manifest.json`) sits next to the files it covers, and a cached
  TensorRT engine is loaded after a check of its fingerprint file, not of a digest of the plan: whoever can write the model
  or engine directories can change what the embedder runs, and the check notices damage, not an attacker.
* **Native code is not covered.** TensorRT, CUDA, NumPy and the tokenizers run native code. Axmem's analysis tools read
  Python, and `trivy` reports only vulnerabilities that already have a fix.
* **Hosted CI does not run the tests.** The GPU, Docker and Qdrant suites run in the `pre-push` hook on the maintainer's
  machine, and `git push --no-verify` skips them ([Quality gates](quality-gates.md#where-the-gates-run)).
* **The release approval is one person today.** The environment `pypi` requires a reviewer, and the only reviewer at the time of
  writing is the lead maintainer ([Releasing Axmem](release.md#what-protects-the-upload)).

## What is out of scope

* The operator's host, its operating system, the NVIDIA driver, Python itself, and anyone with root on the machine.
* Qdrant, and the identity provider or embeddings service you choose: Axmem trusts them as configured.
* Availability under load: the embedder and the reranker serve one call at a time on one GPU, so a flood of valid requests will slow it.
* Anything a user does with a token after it has been issued to them, including sharing it.

## Operating Axmem securely

1. Choose a credential mode. Use `none` only on stdio or on `127.0.0.1` / `::1` with nothing forwarding to it.
2. Serve TLS from any HTTP server that is not on loopback, with `SERVER_SSL_CERTFILE` and `SERVER_SSL_KEYFILE` or with a
   TLS-terminating reverse proxy, and rate-limit in the proxy. A start that warns "Serving plain HTTP" is the reminder.
3. Set `AXMEM_SCOPES` to the least the instance needs, and give tokens narrow `scope` claims.
4. Keep Qdrant on a private network, set `QDRANT_API_KEY`, and use `https://` in `QDRANT_URL` when it is not on the same host.
5. Name the origins you trust in `CORS_ORIGINS`, or leave CORS off.
6. Keep the backup directory, the model directories, the engine cache directories (`EMBEDDING_ENGINE_CACHE_DIR`,
   `RERANKING_ENGINE_CACHE_DIR`) and the TLS key file on storage that only the Axmem user can read, and only it can write. Run
   `axmem` in a directory you trust: it reads a settings file from the working directory, which cannot name the server the CLI
   sends `AXMEM_TOKEN` to (SEC-7) but does set `QDRANT_URL` and the provider URLs of a local-mode command, so a hostile file can
   send the Qdrant key from your environment or your user settings file, and the memories, to a host of its own (SR-16). In a
   checkout you do not trust, read its settings file first, and run `axmem` from another directory.
7. Rotate static tokens by replacing their hashes; verify the release you install ([Checking a release](release.md#checking-a-release)).

More detail is in [Security Best Practices](configuration.md#security-best-practices).

## Secrets and credentials

This is the policy for the credentials the project uses itself, to check, build and release Axmem. The secrets an operator of a
server handles (`AXMEM_TOKEN`, `QDRANT_API_KEY`, provider keys, static token hashes, the TLS key) are covered by
[Secrets](#secrets) (SEC-1 to SEC-7), by [What Axmem does not guarantee](#what-axmem-does-not-guarantee) and by
[Operating Axmem securely](#operating-axmem-securely), and are not repeated here. A leak of an operator's secret through a flaw
in Axmem is a vulnerability: report it as [SECURITY.md](../SECURITY.md) says.

One maintainer does the day-to-day work, so a rule is written only where one person can keep it. A rule that names a test, a gate
or a setting is enforced by it. The others rest on the maintainer's habit and nothing checks them. Text marked
**Target** is a goal, not a guarantee. The GitHub settings below were read with calls that only read, on 2026-10-30; they are
not files of the repository, so nothing here enforces them.

### What exists

| Credential | Kept in | Who uses it, scope, expiry | Rotation and revocation |
|---|---|---|---|
| The GitHub account of each person with admin access | At GitHub. The `Axiumine` organization requires two-factor authentication, and new write access goes only to accounts with a TOTP app, a passkey or a security key, not SMS ([GOVERNANCE.md](../GOVERNANCE.md#access)). | Its holder only. Two accounts are owners of the organization and admins of the repository ([MAINTAINERS.md](../MAINTAINERS.md)): the lead maintainer's, and `@vivi7`, which is not a maintainer. No expiry. | No schedule. On suspicion: change the password, end the sessions, delete the account's tokens and keys. The lead maintainer removes a compromised account's access to the organization and the repository, which [GOVERNANCE.md](../GOVERNANCE.md#access) requires at once. If the lead maintainer's own account is the one at risk, nobody is named to do it; `@vivi7` could as an administrator, but no role of it in this is documented. |
| The fine-grained GitHub token of `gh auth login`, the one credential a release needs ([Once, before the first release](release.md#once-before-the-first-release)) | The login store of `gh`, and git's credential helper (`gh auth setup-git`), on the lead maintainer's release machine. No script of the repository reads or stores it. | The lead maintainer, through the release script. Limited to `Axiumine/axmem`: Contents, Actions and Workflows write, Administration read, and no more than what `publish-ready` reports as needed. It can do no more than its account can. A fine-grained token has an expiry date, chosen when it is made. **Target:** at most 90 days. | Make the new token before the old one expires, run `publish-ready` with it, then delete the old one in GitHub (Settings, Developer settings, fine-grained tokens) and `gh auth logout` of the old login. A missing or too narrow login stops a release before anything is changed. |
| The SSH key and the commit signing key of a maintainer | On the maintainer's machine. The public keys are on their GitHub account. | The maintainer: git over SSH when the remote is SSH, and the signed commits that the rules for `main` require. No expiry. **Target:** protected by a passphrase or held on a hardware key. | No schedule. On suspicion or when the device is lost: remove the public key from the GitHub account and make a new pair. |
| `QODANA_TOKEN` | The git-ignored dotenv file in the repository root of the machine that pushes, or the environment ([Quality gates](quality-gates.md#secrets)). | The `qodana` gate of the `pre-push` hook, on the developer machine only; no workflow reads it. It is a Qodana Cloud project token, which lets a scan upload its report to that project. The project sets no expiry that it checks. | **Target:** regenerated once a year, with the dotenv file updated. On suspicion: delete or regenerate it in the Qodana Cloud project. |
| The LLM-provider login of the test harness (`auth.json` of the `opencode` client) | The `opencode` data directory on the maintainer's machine. The harness copies it into a private temporary directory (0700, files 0600), mounts it into the container of one run and deletes it when the run ends (`test_harness/container.py`); the operator's `QDRANT_API_KEY` is passed to the run's server too (`test_harness/server.py`). | The maintainer, for the UAT harness only; it is no part of the test suites, the gates or the release. Its scope and expiry are those the provider gave the login. | No schedule. On suspicion: `opencode auth logout` or revoke the login at the provider, and delete a temporary directory that a killed run left behind. |
| The PyPI and TestPyPI accounts of the lead maintainer | At pypi.org and, if the rehearsal is used, at test.pypi.org: separate accounts, each with two-factor authentication (PyPI requires it). No API token is ever made ([Once, before the first release](release.md#once-before-the-first-release)). | The lead maintainer, to manage the project: the trusted publisher, yanking, owners. A release never logs in to PyPI. | No schedule. On suspicion: change the password and the second factor, end the sessions, and check the project's Publishing page and API tokens page: only `publish.yml` with the environment `pypi` may be listed, and no token. Delete what you did not make. |

What does not exist, on purpose, because a credential that does not exist cannot leak:

* **No PyPI or TestPyPI token in the release flow.** PyPI trusts a workflow file and an environment name (`publish.yml` and `pypi`;
  `publish-testpypi.yml` and `testpypi` on TestPyPI). The one job that uploads gets an OIDC token (`id-token: write`) that the
  index trades for a short-lived upload token. The environments take only the tags `v*.*.*`, need a reviewer, and do not let
  administrators bypass them ([What protects the upload](release.md#what-protects-the-upload)). To revoke the right to
  upload, delete the trusted publisher in the project's Publishing settings; to pause it, disable the workflow.
* **No release signing key.** cosign signs keyless in CI. The identity is the release workflow at the tag, recorded in Rekor (SUP-3).
* **No GitHub secret.** The repository, Dependabot, the organization and the environments `pypi` and `testpypi` hold no secret,
  and the repository has no deploy key. No workflow uses `secrets.`; the architecture tests of `test_workflows.py`,
  `test_publish_workflow.py` and `test_publish_testpypi_workflow.py` fail on it in the workflows they cover. A workflow gets the
  `GITHUB_TOKEN` that GitHub makes for the run and ends with it. The default workflow permissions of the repository are
  read-only, each workflow declares its own `permissions`, a job adds only what it needs (for example `id-token: write`), and
  every checkout has `persist-credentials: false`.
* **No real credential in the test suites.** The Qdrant containers of the test suites are started on a random loopback port with a
  throwaway key (`tests/qdrant_support.py`) and removed at the end of the run, and `scripts/smoke-release.sh` makes a random
  key and token with `openssl rand` for each run. The settings snapshots mask every field whose name ends in `KEY`, `SECRET`,
  `TOKEN`, `PASSWORD`, `CLIENT_ID` or `HASHES` ([Quality gates](quality-gates.md)).

### Storing

* Nothing secret goes into the repository: not code, docs, tests, fixtures, snapshots, `qodana.yaml`, a workflow, a script, a
  commit message, an issue or a pull request. `.env.example` lists names and placeholders only.
* A secret lives in the process environment, in the git-ignored dotenv file in the repository root (`.gitignore` ignores `.env`
  and `.env.*`, not `.env.example`), or in the login store of the tool that issued it (`gh`). The quality runner reads only the
  keys a gate declares, takes the process environment first and never prints them.
* A new GitHub secret, deploy key or token, or any other new place for a secret, is a change to this section in the same
  change. Hosted CI runs only checks that need no secret ([Quality gates](quality-gates.md#where-the-gates-run)).

### Accessing

* A credential belongs to one person and one account. None is shared, and none is sent in an issue, a message or an e-mail. A
  maintainer who needs one makes their own at the service that issues it.
* A credential has the least scope that works: the token above is limited to one repository and the listed permissions, and a
  change to them is a change to [docs/release.md](release.md) and to this table.
* A credential sits only on the machine that needs it. Hosted CI gets none.
* Today only the lead maintainer holds the release token, the Qodana token and the PyPI accounts. The `@vivi7` account holds none of
  them, and nobody is named to take them over ([GOVERNANCE.md](../GOVERNANCE.md#if-the-lead-is-unavailable)).

### Rotating and revoking

* On a schedule: the table above gives a target interval for the release token and the Qodana token; the others change on suspicion or when a person leaves. **Target:** once a year the lead maintainer repeats the
  read-only checks (the lists of repository, Dependabot, organization and environment secrets and of deploy keys are empty, secret
  scanning and push protection are on, the organization requires two-factor authentication) and updates the date in this section.
  No tool of the project tracks these dates.
* At once, on suspicion: revoke with the last column of the table. Do not wait to learn whether the credential was used.
* At once, when a person leaves or loses access ([GOVERNANCE.md](../GOVERNANCE.md#access)): the lead maintainer removes the person from the
  organization, the repository, `.github/CODEOWNERS` and [MAINTAINERS.md](../MAINTAINERS.md), from the reviewers of the
  environments `pypi` and `testpypi`, and from the PyPI project. The person deletes their tokens, their keys and their dotenv file.
  A fine-grained token can do no more than its account, so removing the account's access ends what the token can do in the
  repository. If the lead maintainer is the one who leaves, nobody is named to do this. The `@vivi7` account, which is not a
  maintainer, can still remove access on GitHub and disable the two publish workflows as an administrator, but it is not an
  owner of the PyPI project, so it cannot remove access there.

### If a credential leaks

1. **Revoke it first**, as the table says, before looking into it. Rewriting history does not revoke anything: `main` blocks force
   pushes (except for the Admin role, [GOVERNANCE.md](../GOVERNANCE.md)), and a secret that was pushed has already been copied. Treat it as public.
2. **Assess.** What could the credential do (its scope in the table), from when to when was it exposed, and was it used? Read the
   account's security log and the repository's pushes, tags, releases and workflow runs, and compare the files PyPI serves with
   `release-digests.json` (`scripts/smoke-release.sh --index pypi` includes that comparison,
   [Install check of a release](release.md#install-check-of-a-release)).
3. **Replace it** with a new credential, and run `publish-ready` after a token or a login changes.
4. **Say so.** If users could be affected, because the credential could publish, tag, change a workflow or alter a release (the
   release token, a GitHub account, a signing key, a PyPI account) and it was used or cannot be shown unused, publish a GitHub
   security advisory as [SECURITY.md](../SECURITY.md#what-happens-next) describes, yank the affected release on PyPI when they may
   be tampered with, and release a fix under a new version number, because PyPI never accepts a file name twice. If only a
   development credential was exposed and nothing a user installs could have changed (the Qodana token), say what happened in the
   commit or issue that replaces it, with no secret in it. Either way the other account with admin access is told the same day.
5. **Close the gap.** Find out how it got there, and add the check that would have caught it.

### Finding a leak early

* GitHub secret scanning and push protection are enabled for the repository (the person who pushes can bypass push protection
  with a reason). Scanning of non-provider patterns and validity checks are off.
* The `trivy` gate scans the working tree for secrets (HIGH and CRITICAL, `trivy.yaml`) in `pre-push` and in the hosted CI job
  `trivy`. Ruff's `S` rules and bandit look for hard-coded passwords in the code; the tests, and the few files that `ruff.toml`
  lists with a reason, are excepted.
* None of these sees a credential pasted into an issue, a comment or a log. Whoever sees one hides it and asks its owner to
  revoke it.
