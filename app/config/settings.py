"""Configuration Management For the Service
"""
from pathlib import Path
from typing import cast

from platformdirs import user_config_dir, user_data_dir
from pydantic import Field, ValidationInfo, field_validator
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
EMBEDDING_PROVIDERS = ("FastEmbed", "Azure", "Google", "OpenAI", "Ollama")
RERANKING_PROVIDERS = ("FastEmbed", "HTTP")
LOG_FORMATS = ("console", "json")
_CHOICES = {
    "EMBEDDING_PROVIDER": EMBEDDING_PROVIDERS,
    "RERANKING_PROVIDER": RERANKING_PROVIDERS,
    "LOG_FORMAT": LOG_FORMATS,
}

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
    SERVER_HOST: str = "0.0.0.0"  # nosec B104 - documented container default; set SERVER_HOST to narrow it
    SERVER_PORT: int = 8020
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"

    # CORS Configuration (HTTP transport only)
    CORS_ENABLED: bool = False
    CORS_ORIGINS: list[str] = ["*"]

    # Database Configuration
    DATABASE: str = "SQLite"  # "Postgres" or "SQLite"

    # Postgres Configuration
    POSTGRES_HOST: str = "127.0.0.1"  # 127.0.0.1 for local, forgetful-db for Docker
    PGPORT: int = 5099
    POSTGRES_DB: str = "forgetful"
    POSTGRES_USER: str = "forgetful"
    POSTGRES_PASSWORD: str = "forgetful"

    # SQLite Configuration
    SQLITE_PATH: str = str(_default_data_dir / "forgetful.db")  # Platform-specific path
    SQLITE_MEMORY: bool = False  # Use :memory: database (for testing)

    # Backup Configuration (re-embed backups, SQLite and Postgres)
    # Empty keeps backups in the directory of SQLITE_PATH. Point this at persistent storage
    # when that directory is not (e.g. a Postgres deployment in a container).
    BACKUP_DIR: str = ""

    DB_LOGGING: bool = False

    # Auth Configuration
    DEFAULT_USER_ID: str = "default-user-id"
    DEFAULT_USER_NAME: str = "default-user-name"
    DEFAULT_USER_EMAIL: str = "default-user-email"

    # FastMCP Auth Provider Configuration
    # Set FASTMCP_SERVER_AUTH to enable authentication (omit for no-auth default user mode)
    FASTMCP_SERVER_AUTH: str = ""              # Fully-qualified provider class path

    # GitHub OAuth Provider
    FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID: str = ""
    FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET: str = ""
    FASTMCP_SERVER_AUTH_GITHUB_BASE_URL: str = ""
    FASTMCP_SERVER_AUTH_GITHUB_REQUIRED_SCOPES: str = ""

    # Google OAuth Provider
    FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID: str = ""
    FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET: str = ""
    FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL: str = ""
    FASTMCP_SERVER_AUTH_GOOGLE_REQUIRED_SCOPES: str = ""

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

    # OAuth Storage Configuration
    OAUTH_STORAGE_PATH: str = str(_default_data_dir / "oauth")  # Platform-specific path for OAuth tokens

    # Token Cache Configuration (for HTTP/REST auth performance)
    TOKEN_CACHE_ENABLED: bool = True           # Enable/disable token caching
    TOKEN_CACHE_TTL_SECONDS: int = 300         # 5 minutes default
    TOKEN_CACHE_MAX_SIZE: int = 1000           # Maximum cached tokens

    # Scoped Permissions Configuration
    FORGETFUL_SCOPES: str = "*"               # Comma-separated scopes (e.g. "read", "write:memories", "read,write:projects")

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

    # Project Configuration
    PROJECT_DESCRIPTION_MAX_LENGTH: int = 5000  # Text field, no DB limit - reasonable cap
    PROJECT_NOTES_MAX_LENGTH: int = 4000        # Text field, no DB limit - reasonable cap

    # Provenance Defaults (auto-applied to all writes when set via MCP env config)
    ENCODING_AGENT: str = ""        # Software running the agent (e.g., "OpenCode")
    ENCODING_VERSION: str = ""      # Version of that software (e.g., "1.3.13")
    AGENT_ID: str = ""              # Agent identity (e.g., "CodeAgentUltra")
    AGENT_VERSION: str = ""         # Agent version (e.g., "1.0")
    AGENT_MODEL: str = ""           # LLM model (e.g., "claude-sonnet-4-6")
    ENFORCE_ENV_OVERWRITE: bool = False  # When True, env values override agent-provided values

    # Search Configuration
    EMBEDDING_PROVIDER: str = "FastEmbed" # FastEmbed | Azure | Google | OpenAI | Ollama
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSIONS: int = 384
    EMBEDDING_ONNX_PROVIDERS: str = ""  # FastEmbed only; comma-separated ONNX Runtime providers, empty = default
    # Per-request timeout (seconds) for the remote providers (Azure | Google | OpenAI | Ollama).
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

    # RERANKING
    RERANKING_ENABLED: bool = True
    RERANKING_PROVIDER: str = "FastEmbed"        # FastEmbed | HTTP
    RERANKING_URL: str = ""                      # custom endpoints (e.g. http://localhost:8080/v1/rerank)
    RERANKING_API_KEY: str = ""
    RERANKING_TIMEOUT: float = Field(default=60.0, gt=0, allow_inf_nan=False)  # HTTP rerank request timeout (seconds)
    RERANKING_MODEL: str = "Xenova/ms-marco-MiniLM-L-12-v2"
    # Local FastEmbed only; HTTP concurrency is controlled by the remote server.
    RERANKING_THREADS: int = Field(default=1, ge=1)  # ONNX inference threads
    RERANKING_WORKERS: int = Field(default=1, ge=1)  # Concurrent reranking jobs per adapter
    RERANKING_ONNX_PROVIDERS: str = ""  # Comma-separated ONNX Runtime providers, empty = FastEmbed default
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

    @field_validator(*PROVENANCE_MAX_LENGTHS)
    @classmethod
    def _validate_provenance_length(cls, value: str, info: ValidationInfo) -> str:
        field_name = _field_name(info)
        limit = PROVENANCE_MAX_LENGTHS[field_name]
        if len(value) > limit:
            raise ValueError(f"{field_name} must be at most {limit} characters (got {len(value)})")
        return value

    @field_validator("RERANKING_ONNX_PROVIDERS", "EMBEDDING_ONNX_PROVIDERS")
    @classmethod
    def _validate_onnx_providers(cls, value: str) -> str:
        parse_onnx_providers(value)
        return value

    @property
    def embedding_onnx_providers(self) -> list[str] | None:
        return parse_onnx_providers(self.EMBEDDING_ONNX_PROVIDERS)

    @property
    def reranking_onnx_providers(self) -> list[str] | None:
        return parse_onnx_providers(self.RERANKING_ONNX_PROVIDERS)

    """Pydantic Configuration"""

    model_config = SettingsConfigDict(
        env_file=[
            ".env",  # Local override
            "docker/.env",  # Development/Docker
            str(_default_config_dir / ".env"),  # User config
        ],
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
