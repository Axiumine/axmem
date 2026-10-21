"""Configuration Management For the Service
"""
import re
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from platformdirs import user_config_dir, user_data_dir
from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

from app.version import get_version

# NOTE: We deliberately do NOT call dotenv.load_dotenv() here. It promotes a
# discovered .env into os.environ, which pydantic-settings ranks above every
# env_file entry - letting a stray .env in the run directory outrank the user
# config that `axmem auth login` writes to ~/.config/axmem/.env. The
# env_file list below already encodes the documented precedence (user config
# last => highest, since DotEnvSettingsSource merges files in order).

# Platform-specific default paths
_default_data_dir = Path(user_data_dir("axmem", ensure_exists=False))
_default_config_dir = Path(user_config_dir("axmem", ensure_exists=False))
# The settings file of the user, and the only one besides the one in the working directory that Axmem reads
USER_CONFIG_FILE = _default_config_dir / ".env"


# Accepted values of the enumerated settings; the first entry is the default. Matching is
# case-insensitive and the spelling below is the canonical one the rest of the code compares to.
EMBEDDING_PROVIDERS = ("TensorRT", "Azure", "Google", "OpenAI", "Ollama", "gateway")
RERANKING_PROVIDERS = ("TensorRT", "HTTP")
LOG_FORMATS = ("console", "json")
LOG_LEVELS = ("INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL")
# AUTH_MODE has no default choice: a blank value stays blank and the server refuses to start
AUTH_MODES = ("none", "jwt", "introspection", "static")
_CHOICES = {
    "EMBEDDING_PROVIDER": EMBEDDING_PROVIDERS,
    "RERANKING_PROVIDER": RERANKING_PROVIDERS,
    "LOG_FORMAT": LOG_FORMATS,
    "LOG_LEVEL": LOG_LEVELS,
}

# The longest tenant name accepted from a credential (a tenant is identified by its name)
TENANT_MAX_LENGTH = 255
# Collection names are <prefix>_memories_<generation>, which Qdrant limits to 255 characters
_COLLECTION_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

# ENCODING_* / AGENT_* defaults are copied into the Create/Update models of every object type,
# whose provenance fields enforce these maximum lengths.
PROVENANCE_MAX_LENGTHS = {
    "ENCODING_AGENT": 100,
    "ENCODING_VERSION": 50,
    "AGENT_ID": 100,
    "AGENT_VERSION": 50,
    "AGENT_MODEL": 100,
}

def canonical_choice(setting: str, value: str, choices: tuple[str, ...]) -> str:
    """Resolve ``value`` to its canonical spelling in ``choices``, ignoring case and padding.

    A blank value means "not set" and resolves to the default (the first choice). Anything
    else that is not a choice raises, so a typo fails fast instead of silently selecting a
    different provider or format. The error names the setting and never repeats the value, which may be a
    credential pasted into the wrong setting.
    """
    wanted = (value or "").strip().lower()
    if not wanted:
        return choices[0]
    for choice in choices:
        if choice.lower() == wanted:
            return choice
    raise ValueError(f"Unsupported {setting} setting. Must be one of: {', '.join(choices)}")


def _field_name(info: ValidationInfo) -> str:
    """Name of the field a field validator is running for."""
    # pydantic leaves field_name None only for validators that are not bound to a field;
    # every validator in this module is registered with @field_validator on named fields.
    return cast(str, info.field_name)


class Settings(BaseSettings):
    # Application Info
    SERVICE_NAME: str = "Axmem"
    SERVICE_VERSION: str = get_version()
    SERVICE_DESCRIPTION: str = "Axmem Memory Service"

    # Server Configuration
    SERVER_HOST: str = "0.0.0.0"  # nosec B104 - documented default; set SERVER_HOST to narrow it
    SERVER_PORT: int = 8020
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"

    # Largest request body the HTTP transport accepts, in bytes (REST routes and /mcp); a larger one is answered 413
    REQUEST_BODY_MAX_BYTES: int = Field(default=4_194_304, ge=1024)

    # CORS Configuration (HTTP transport only)
    CORS_ENABLED: bool = False
    CORS_ORIGINS: list[str] = ["*"]
    # Whether a browser may send credentials (cookies, HTTP authentication) with a cross-origin call. Axmem reads a
    # bearer token from a header, which needs none, so it is off; on, it needs named origins (see the validator)
    CORS_ALLOW_CREDENTIALS: bool = False
    # Host names besides localhost and loopback addresses that a server running AUTH_MODE=none answers to
    # (for example host.docker.internal, for a client in a container on this machine). See loopback_guard.
    AXMEM_ALLOWED_HOSTS: list[str] = []

    # Qdrant: the only datastore. Memories, their links and their vectors all live in it.
    # One URL for a single node, or several comma separated URLs for the nodes of a cluster
    # (requests are spread over them and move on to the next one when a node is unreachable).
    QDRANT_URL: str = "http://127.0.0.1:6333"
    # An API key is instance wide: it admits every tenant, tenant isolation is enforced by this service.
    QDRANT_API_KEY: SecretStr = SecretStr("")
    # Collections are named <prefix>_memories (an alias onto the live generation) and <prefix>_links.
    QDRANT_COLLECTION_PREFIX: str = "axmem"
    # Collection layout, fixed when the collection is created. The defaults suit one node; the verified
    # cluster profile for three nodes is 3 shards, 3 replicas and a write consistency factor of 2
    # (docs/configuration.md). The service refuses to start when an existing collection differs.
    QDRANT_SHARD_NUMBER: int = Field(default=1, ge=1)
    QDRANT_REPLICATION_FACTOR: int = Field(default=1, ge=1)
    QDRANT_WRITE_CONSISTENCY_FACTOR: int = Field(default=1, ge=1)
    # Per-request timeout in seconds; keep it above the 7-8 s a cluster needs to forward a write to its leader.
    QDRANT_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    # Attempts for a write that failed with an ambiguous error (timeout, reset, 5xx); retries reuse the claim token.
    QDRANT_WRITE_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    # Directory `axmem backup` writes its snapshot sets to. Empty keeps them in the data directory of the platform.
    # `axmem export` does not use it: it writes the file named on the command line.
    BACKUP_DIR: str = ""

    # Authentication. The server fails closed: with AUTH_MODE unset it refuses to start.
    #   none          one local tenant (DEFAULT_TENANT) for every request; accepted only on stdio
    #                 or when the HTTP server binds a loopback address
    #   jwt           signed bearer tokens (signature, exp, issuer and audience are checked; issuer and audience
    #                 are required); the tenant is the claim tenant_id, else tenant, else sub
    #   introspection bearer tokens checked at an RFC 7662 endpoint; same tenant claims
    #   static        fixed bearer tokens, one per tenant, configured as SHA-256 hashes
    AUTH_MODE: str = ""
    DEFAULT_TENANT: str = Field(default="default", min_length=1, max_length=TENANT_MAX_LENGTH)

    # Static bearer tokens (AUTH_MODE=static): tenant -> SHA-256 hex digest of that tenant's token.
    # Set it as a JSON object, e.g. '{"acme": "<64 hex characters>"}'. The plain token is never configured.
    AUTH_STATIC_TOKEN_HASHES: dict[str, str] = {}

    # JWT Verification
    FASTMCP_SERVER_AUTH_JWT_JWKS_URI: str = ""
    FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY: str = ""
    FASTMCP_SERVER_AUTH_JWT_ISSUER: str = ""
    FASTMCP_SERVER_AUTH_JWT_AUDIENCE: str = ""
    FASTMCP_SERVER_AUTH_JWT_REQUIRED_SCOPES: str = ""

    # Token Introspection (RFC 7662)
    FASTMCP_SERVER_AUTH_INTROSPECTION_URL: str = ""
    FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID: str = ""
    FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET: str = ""
    FASTMCP_SERVER_AUTH_INTROSPECTION_REQUIRED_SCOPES: str = ""
    FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE: str = ""  # when set, the answer's aud claim must name it

    # Token Cache Configuration (for HTTP/REST auth performance)
    TOKEN_CACHE_ENABLED: bool = True           # Enable/disable token caching
    TOKEN_CACHE_TTL_SECONDS: int = 300         # 5 minutes default
    TOKEN_CACHE_MAX_SIZE: int = 1000           # Maximum cached tokens

    # Scoped Permissions Configuration
    AXMEM_SCOPES: str = "*"               # Comma-separated scopes (e.g. "read", "write:memories", "read:memories,write:memories")

    # CLI Remote Mode Configuration
    AXMEM_SERVER: str = ""                # Remote deployment URL for the CLI (empty = local mode)

    # MCP descriptor presentation
    MCP_DESCRIPTOR_MODE: str = "verbose"       # "verbose" (default) or "compact" meta-tool docstrings

    # Memory Configuration
    MEMORY_TITLE_MAX_LENGTH: int = 200      # Must be "easily titled" - scannable
    MEMORY_CONTENT_MAX_LENGTH: int = 2000   # ~300-400 words - single concept
    MEMORY_CONTEXT_MAX_LENGTH: int = 500    # Brief contextual description
    MEMORY_KEYWORDS_MAX_COUNT: int = 10     # For semantic clustering
    MEMORY_TAGS_MAX_COUNT: int = 10         # For categorization
    MEMORY_KEYWORD_MAX_LENGTH: int = 100    # Characters per keyword - search terms, not sentences
    MEMORY_TAG_MAX_LENGTH: int = 100        # Characters per tag - short category labels
    MEMORY_SOURCE_FILE_MAX_LENGTH: int = 4096  # Characters per source file path (Linux PATH_MAX)
    MEMORY_SOURCE_FILES_MAX_COUNT: int = Field(default=50, ge=1)       # Source files per memory
    MEMORY_QUERY_MAX_LENGTH: int = Field(default=2000, ge=1)           # Characters of a search query
    MEMORY_QUERY_CONTEXT_MAX_LENGTH: int = Field(default=2000, ge=1)   # Characters of the reason given with a search
    MEMORY_OBSOLETE_REASON_MAX_LENGTH: int = Field(default=500, ge=1)  # Characters of the reason for marking obsolete
    MEMORY_LINKS_MAX_PER_REQUEST: int = Field(default=50, ge=1)        # Memories one link request may name
    MEMORY_TOKEN_BUDGET: int = 8000         # token budget for retrieved memories (to protect context window)
    MEMORY_MAX_MEMORIES: int = 20           # maximum number of memories that can be retrieved from a query
    # Seconds the start-up waits for the tiktoken encoding of the token counter (its data is fetched on first use unless
    # TIKTOKEN_CACHE_DIR, a variable of the process environment, holds it); the server refuses to start after that
    TIKTOKEN_LOAD_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    MEMORY_NUM_AUTO_LINK: int = 3           # number of memories to automatically link
    MEMORY_SIMILARITY_THRESHOLD: float = 0.7  # minimum cosine similarity for auto-links
    OBSOLETE_WARNING_ENABLED: bool = True  # create_memory warns when content matches superseded memories
    OBSOLETE_WARNING_THRESHOLD: float = 0.89  # minimum cosine similarity for obsolete-memory warnings

    # Provenance Defaults (auto-applied to all writes when set via MCP env config)
    ENCODING_AGENT: str = ""        # Software running the agent (e.g., "OpenCode")
    ENCODING_VERSION: str = ""      # Version of that software (e.g., "1.3.13")
    AGENT_ID: str = ""              # Agent identity (e.g., "CodeAgentUltra")
    AGENT_VERSION: str = ""         # Agent version (e.g., "1.0")
    AGENT_MODEL: str = ""           # LLM model (e.g., "claude-sonnet-4-6")
    ENFORCE_ENV_OVERWRITE: bool = False  # When True, env values override agent-provided values

    # Search Configuration
    EMBEDDING_PROVIDER: str = "TensorRT" # TensorRT | Azure | Google | OpenAI | Ollama | gateway
    # TensorRT: the Hugging Face repository the ONNX model and tokenizer come from. Other providers: their model name.
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSIONS: int = 384              # TensorRT checks it against the width the model returns at startup
    # Local TensorRT embeddings only.
    EMBEDDING_TF32: bool = False                 # True lets the engine use TF32 tensor cores (faster, not exact fp32)
    EMBEDDING_MODEL_DIR: str = str(_default_data_dir / "models" / "embedder")  # model.onnx and tokenizer.json, fetched once
    EMBEDDING_ENGINE_CACHE_DIR: str = str(_default_data_dir / "models" / "tensorrt")  # built engines, one directory per fingerprint
    # Per-request timeout (seconds) for the remote providers (Azure | Google | OpenAI | Ollama | gateway).
    # The OpenAI/Azure SDK clients retry failed requests, so a call can take (retries + 1) x this value.
    EMBEDDING_REQUEST_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0, allow_inf_nan=False)

    # AZURE EMBEDDING PROVIDER CONFIG
    AZURE_ENDPOINT: str = ""
    AZURE_DEPLOYMENT: str = ""
    AZURE_API_VERSION: str = ""
    AZURE_API_KEY: str = ""
    AZURE_SUPPORTS_DIMENSIONS: bool = True       # False for deployments that reject it (text-embedding-ada-002)

    # GOOGLE EMBEDDING PROVIDER CONFIG
    GOOGLE_AI_API_KEY: str = ""

    # OPENAI EMBEDDING PROVIDER CONFIG
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""                    # custom endpoint (e.g. http://localhost:8080/v1)
    OPENAI_SUPPORTS_DIMENSIONS: bool = True      # False for endpoints that don't support it (llama.cpp)

    # OLLAMA EMBEDDING PROVIDER CONFIG
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # GATEWAY EMBEDDING PROVIDER CONFIG (HTTP embeddings gateway; read only when EMBEDDING_PROVIDER=gateway)
    GATEWAY_URL: str = ""                        # base URL, e.g. https://embeddings.example.net
    GATEWAY_PATH: str = "/api/v1/embeddings"
    GATEWAY_TOKEN: SecretStr = SecretStr("")     # credential; sent as "Bearer <token>" in Authorization
    GATEWAY_AUTH_HEADER: str = "Authorization"   # any other name receives the raw token
    GATEWAY_TENANT_HEADER: str = "X-Tenant-Id"
    GATEWAY_TENANT: str = ""                     # sent in GATEWAY_TENANT_HEADER only when set
    GATEWAY_BACKEND: str = ""
    GATEWAY_MODEL: str = ""
    GATEWAY_AGENT_ID: str = ""
    GATEWAY_METADATA: str = "{}"                 # JSON object of string values, sent with every request; only the gateway adapter parses it
    GATEWAY_SUPPORTS_DIMENSIONS: bool = False    # True sends EMBEDDING_DIMENSIONS as the "dimensions" field

    # RERANKING
    RERANKING_ENABLED: bool = True
    RERANKING_PROVIDER: str = "TensorRT"         # TensorRT | HTTP
    RERANKING_URL: str = ""                      # custom endpoints (e.g. http://localhost:8080/v1/rerank)
    RERANKING_API_KEY: str = ""
    RERANKING_TIMEOUT: float = Field(default=60.0, gt=0, allow_inf_nan=False)  # HTTP rerank request timeout (seconds)
    RERANKING_MODEL: str = "Xenova/ms-marco-MiniLM-L-12-v2"
    # Local TensorRT only; HTTP concurrency is controlled by the remote server.
    RERANKING_WORKERS: int = Field(default=1, ge=1)  # Concurrent reranking jobs (one execution context each)
    RERANKING_TF32: bool = False                 # True lets the engine use TF32 tensor cores (faster, not exact fp32)
    RERANKING_MODEL_DIR: str = str(_default_data_dir / "models" / "reranker")  # model.onnx and tokenizer.json, fetched once
    RERANKING_ENGINE_CACHE_DIR: str = str(_default_data_dir / "models" / "tensorrt")  # built engines, one directory per fingerprint
    DENSE_SEARCH_CANDIDATES: int = 20 # number of candidates to retrieve from the dense search

    @field_validator("QDRANT_URL")
    @classmethod
    def _validate_qdrant_url(cls, value: str) -> str:
        urls = [url.strip().rstrip("/") for url in value.split(",") if url.strip()]
        if not urls:
            raise ValueError("QDRANT_URL must name at least one Qdrant node, e.g. http://127.0.0.1:6333")
        for url in urls:
            if not url.startswith(("http://", "https://")):
                raise ValueError("QDRANT_URL entries must start with http:// or https://")
            parts = urlsplit(url)
            try:
                valid = bool(parts.hostname) and parts.port != 0
            except ValueError:  # a port that is not a number or beyond 65535
                valid = False
            if not valid:
                raise ValueError("QDRANT_URL entries must name a host and, if they give one, a port from 1 to 65535")
        return ",".join(dict.fromkeys(urls))

    @field_validator("QDRANT_COLLECTION_PREFIX")
    @classmethod
    def _validate_collection_prefix(cls, value: str) -> str:
        if not _COLLECTION_PREFIX.fullmatch(value):
            raise ValueError(
                "QDRANT_COLLECTION_PREFIX must be 1 to 64 letters, digits, underscores or hyphens, "
                "starting with a letter or digit",
            )
        return value

    @model_validator(mode="after")
    def _validate_cors_credentials_need_named_origins(self) -> Settings:
        # A "*" answers every web page; credentials on top would let any page act with the visitor's cookies
        if self.CORS_ALLOW_CREDENTIALS and "*" in self.CORS_ORIGINS:
            raise ValueError(
                "CORS_ALLOW_CREDENTIALS=true needs CORS_ORIGINS to name the origins it trusts: a '*' answers every web "
                "page, and credentials are never sent to every page. Name the origins, or set CORS_ALLOW_CREDENTIALS=false",
            )
        return self

    @model_validator(mode="after")
    def _validate_write_consistency(self) -> Settings:
        if self.QDRANT_WRITE_CONSISTENCY_FACTOR > self.QDRANT_REPLICATION_FACTOR:
            raise ValueError("QDRANT_WRITE_CONSISTENCY_FACTOR cannot exceed QDRANT_REPLICATION_FACTOR")
        return self

    @property
    def backup_directory(self) -> Path:
        """Where ``axmem backup`` writes its snapshot sets (an export goes to the file named on its command line)."""
        return Path(self.BACKUP_DIR) if self.BACKUP_DIR else _default_data_dir / "backups"

    @property
    def qdrant_urls(self) -> list[str]:
        """The Qdrant node URLs, in the order configured."""
        return self.QDRANT_URL.split(",")

    @field_validator("OBSOLETE_WARNING_THRESHOLD")
    @classmethod
    def _validate_obsolete_warning_threshold(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError("OBSOLETE_WARNING_THRESHOLD must be in (0, 1]")
        return value

    @field_validator("EMBEDDING_PROVIDER", "RERANKING_PROVIDER", "LOG_FORMAT", "LOG_LEVEL")
    @classmethod
    def _validate_choice_setting(cls, value: str, info: ValidationInfo) -> str:
        field_name = _field_name(info)
        return canonical_choice(field_name, value, _CHOICES[field_name])

    @field_validator("AUTH_MODE")
    @classmethod
    def _validate_auth_mode(cls, value: str) -> str:
        # Blank means "not configured" and must stay blank: there is no default mode to fall back to
        if not value.strip():
            return ""
        return canonical_choice("AUTH_MODE", value, AUTH_MODES)

    @field_validator("DEFAULT_TENANT")
    @classmethod
    def _validate_default_tenant(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DEFAULT_TENANT must not be blank")
        return value.strip()

    @field_validator("AUTH_STATIC_TOKEN_HASHES")
    @classmethod
    def _validate_static_token_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        # Messages never repeat a configured value: a plain token pasted here by mistake must not be echoed
        hashes: dict[str, str] = {}
        for tenant, digest in value.items():
            name = tenant.strip()
            if not name or len(name) > TENANT_MAX_LENGTH:
                raise ValueError(
                    f"AUTH_STATIC_TOKEN_HASHES tenant names must be 1 to {TENANT_MAX_LENGTH} characters",
                )
            if name in hashes:
                raise ValueError("AUTH_STATIC_TOKEN_HASHES names the same tenant twice")
            normalised = digest.strip().lower()
            if not _SHA256_HEX.fullmatch(normalised):
                raise ValueError(
                    "AUTH_STATIC_TOKEN_HASHES values must be 64-character hexadecimal SHA-256 "
                    "digests of the tokens, never the tokens themselves",
                )
            hashes[name] = normalised
        if len(set(hashes.values())) != len(hashes):
            raise ValueError(
                "AUTH_STATIC_TOKEN_HASHES gives two tenants the same token: one token belongs to one tenant",
            )
        return hashes

    @field_validator(*PROVENANCE_MAX_LENGTHS)
    @classmethod
    def _validate_provenance_length(cls, value: str, info: ValidationInfo) -> str:
        field_name = _field_name(info)
        limit = PROVENANCE_MAX_LENGTHS[field_name]
        if len(value) > limit:
            raise ValueError(f"{field_name} must be at most {limit} characters (got {len(value)})")
        return value

    """Pydantic Configuration"""

    model_config = SettingsConfigDict(
        env_file=[
            ".env",  # Local override
            str(USER_CONFIG_FILE),  # User config
        ],
        env_file_encoding="utf-8",
        extra="ignore",
        # Settings hold credentials (API keys, token hashes): validation errors must not echo them
        hide_input_in_errors=True,
    )



def describe_settings_failure(exc: Exception) -> str:
    """One line that says why the settings could not be loaded, without repeating any configured value.

    ``hide_input_in_errors`` keeps the values out of pydantic's errors, and only ``loc`` and ``msg`` are used
    here, so a credential pasted into the wrong setting is never echoed.
    """
    if isinstance(exc, ValidationError):
        problems = []
        for error in exc.errors(include_url=False, include_context=False, include_input=False):
            name = ".".join(str(part) for part in error["loc"])
            message = error["msg"].removeprefix("Value error, ")
            problems.append(message if name.lower() in message.lower() else f"{name}: {message}")
        return "; ".join(problems)
    if isinstance(exc, OSError):
        return f"a settings file cannot be read: {exc.strerror or exc} ({exc.filename})"
    if isinstance(exc, UnicodeError):
        return "a settings file is not valid UTF-8; save it as UTF-8"
    return str(exc)


def load_settings() -> Settings:
    """The settings of the environment and the dotenv files, or an exit with the one-line cause.

    Every command builds its settings when this module is imported. A bad value (a typo in a provider, a
    port that is not a number), a dotenv file that cannot be read or a JSON setting that does not parse is an
    operator mistake, so it ends the process with ``Error: <cause>`` and exit code 1, never a traceback.
    """
    try:
        return Settings()
    except (ValidationError, SettingsError, OSError, UnicodeError) as exc:
        raise SystemExit(f"Error: {describe_settings_failure(exc)}") from exc


settings = load_settings()
