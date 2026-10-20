"""Configuration Management For the Service
"""
import re
from pathlib import Path
from typing import cast

from platformdirs import user_config_dir, user_data_dir
from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.version import get_version

# NOTE: We deliberately do NOT call dotenv.load_dotenv() here. It promotes a
# discovered .env into os.environ, which pydantic-settings ranks above every
# env_file entry - letting a stray .env in the run directory outrank the user
# config that `forgetful auth login` writes to ~/.config/forgetful/.env. The
# env_file list below already encodes the documented precedence (user config
# last => highest, since DotEnvSettingsSource merges files in order).

# Platform-specific default paths
_default_data_dir = Path(user_data_dir("forgetful", ensure_exists=False))
_default_config_dir = Path(user_config_dir("forgetful", ensure_exists=False))


# Accepted values of the enumerated settings; the first entry is the default. Matching is
# case-insensitive and the spelling below is the canonical one the rest of the code compares to.
EMBEDDING_PROVIDERS = ("FastEmbed", "Azure", "Google", "OpenAI", "Ollama", "gateway")
RERANKING_PROVIDERS = ("TensorRT", "HTTP")
LOG_FORMATS = ("console", "json")
# AUTH_MODE has no default choice: a blank value stays blank and the server refuses to start
AUTH_MODES = ("none", "jwt", "introspection", "static")
_CHOICES = {
    "EMBEDDING_PROVIDER": EMBEDDING_PROVIDERS,
    "RERANKING_PROVIDER": RERANKING_PROVIDERS,
    "LOG_FORMAT": LOG_FORMATS,
}

# The longest tenant name the tenant record can hold (users.external_id is VARCHAR(255))
TENANT_MAX_LENGTH = 255
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
    different provider or format.
    """
    wanted = (value or "").strip().lower()
    if not wanted:
        return choices[0]
    for choice in choices:
        if choice.lower() == wanted:
            return choice
    raise ValueError(f"Unsupported {setting} setting: {value!r}. Must be one of: {', '.join(choices)}")


def _field_name(info: ValidationInfo) -> str:
    """Name of the field a field validator is running for."""
    # pydantic leaves field_name None only for validators that are not bound to a field;
    # every validator in this module is registered with @field_validator on named fields.
    return cast(str, info.field_name)


def parse_onnx_providers(value: str | None) -> list[str] | None:
    """Split a comma-separated ONNX Runtime provider list; None keeps FastEmbed's default."""
    if value is None or not value.strip():
        return None
    providers = [name.strip() for name in value.split(",")]
    if any(not name for name in providers):
        raise ValueError("ONNX provider lists must not contain empty entries")
    return providers


class Settings(BaseSettings):
    # Application Info
    SERVICE_NAME: str = "Forgetful"
    SERVICE_VERSION: str = get_version()
    SERVICE_DESCRIPTION: str = "Forgetful Memory Service"

    # Server Configuration
    SERVER_HOST: str = "0.0.0.0"  # nosec B104 - documented default; set SERVER_HOST to narrow it
    SERVER_PORT: int = 8020
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"

    # CORS Configuration (HTTP transport only)
    CORS_ENABLED: bool = False
    CORS_ORIGINS: list[str] = ["*"]

    # Database Configuration
    DATABASE: str = "SQLite"  # "Postgres" or "SQLite"

    # Postgres Configuration
    POSTGRES_HOST: str = "127.0.0.1"
    PGPORT: int = 5099
    POSTGRES_DB: str = "forgetful"
    POSTGRES_USER: str = "forgetful"
    POSTGRES_PASSWORD: str = "forgetful"

    # SQLite Configuration
    SQLITE_PATH: str = str(_default_data_dir / "forgetful.db")  # Platform-specific path
    SQLITE_MEMORY: bool = False  # Use :memory: database (for testing)

    # Backup Configuration (re-embed backups, SQLite and Postgres)
    # Empty keeps backups in the directory of SQLITE_PATH. Point this at persistent storage
    # when that directory is not (for example when only PostgreSQL is used on this host).
    BACKUP_DIR: str = ""

    DB_LOGGING: bool = False

    # Authentication. The server fails closed: with AUTH_MODE unset it refuses to start.
    #   none          one local tenant (DEFAULT_TENANT) for every request; accepted only on stdio
    #                 or when the HTTP server binds a loopback address
    #   jwt           signed bearer tokens; the tenant is the claim tenant_id, else tenant, else sub
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

    # Token Cache Configuration (for HTTP/REST auth performance)
    TOKEN_CACHE_ENABLED: bool = True           # Enable/disable token caching
    TOKEN_CACHE_TTL_SECONDS: int = 300         # 5 minutes default
    TOKEN_CACHE_MAX_SIZE: int = 1000           # Maximum cached tokens

    # Scoped Permissions Configuration
    FORGETFUL_SCOPES: str = "*"               # Comma-separated scopes (e.g. "read", "write:memories", "read:memories,write:memories")

    # CLI Remote Mode Configuration
    FORGETFUL_SERVER: str = ""                # Remote deployment URL for the CLI (empty = local mode)

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
    MEMORY_TOKEN_BUDGET: int = 8000         # token budget for retrieved memories (to protect context window)
    MEMORY_MAX_MEMORIES: int = 20           # maximum number of memories that can be retrieved from a query
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
    EMBEDDING_PROVIDER: str = "FastEmbed" # FastEmbed | Azure | Google | OpenAI | Ollama | gateway
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSIONS: int = 384
    EMBEDDING_ONNX_PROVIDERS: str = ""  # FastEmbed only; comma-separated ONNX Runtime providers, empty = default
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
    GATEWAY_METADATA: dict[str, str] = {}        # JSON object of string values, sent with every request
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

    # FASTEMBED CACHE CONFIGURATION
    FASTEMBED_CACHE_DIR: str = str(_default_data_dir / "models" / "fastembed")
    FASTEMBED_LOCAL_FILES_ONLY: bool = False      # When True, FastEmbed only loads models from local cache

    @field_validator("OBSOLETE_WARNING_THRESHOLD")
    @classmethod
    def _validate_obsolete_warning_threshold(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError("OBSOLETE_WARNING_THRESHOLD must be in (0, 1]")
        return value

    @field_validator("EMBEDDING_PROVIDER", "RERANKING_PROVIDER", "LOG_FORMAT")
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

    @field_validator("EMBEDDING_ONNX_PROVIDERS")
    @classmethod
    def _validate_onnx_providers(cls, value: str) -> str:
        parse_onnx_providers(value)
        return value

    @property
    def embedding_onnx_providers(self) -> list[str] | None:
        return parse_onnx_providers(self.EMBEDDING_ONNX_PROVIDERS)

    """Pydantic Configuration"""

    model_config = SettingsConfigDict(
        env_file=[
            ".env",  # Local override
            str(_default_config_dir / ".env"),  # User config
        ],
        env_file_encoding="utf-8",
        extra="ignore",
        # Settings hold credentials (API keys, token hashes): validation errors must not echo them
        hide_input_in_errors=True,
    )

settings = Settings()
