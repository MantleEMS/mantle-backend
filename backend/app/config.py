from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://mantle:mantle_secret@postgres:5432/mantle_ems"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # JWT
    SECRET_KEY: str = "change-me-use-a-strong-random-secret-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    MOBILE_ACCESS_TOKEN_EXPIRE_DAYS: int = 365

    # Uploads
    UPLOADS_DIR: str = "uploads"

    # Logs
    LOGS_DIR: str = "logs"

    # Firebase
    FIREBASE_CREDENTIALS_PATH: Optional[str] = None

    # Seed
    RUN_SEED: bool = True

    # AI / LLM
    AI_MODE: str = "scripted"                  # scripted | llm
    LLM_PROVIDER: str = "ollama"               # ollama | anthropic | bedrock
    LLM_MODEL: str = "llama3.1:8b"             # provider-specific model id
    LLM_BASE_URL: str = "http://localhost:11434"  # Ollama server URL
    ANTHROPIC_API_KEY: Optional[str] = None    # sk-ant-... for Anthropic
    AWS_REGION: str = "us-east-1"              # for Bedrock
    LLM_TEMPERATURE: float = 0.0               # 0 = deterministic SOP execution
    LLM_TIMEOUT: int = 30                      # seconds before fallback to scripted
    LLM_MAX_TOKENS: int = 4096
    LLM_MAX_ITERATIONS: int = 15               # max agent loop iterations
    LLM_ADAPTIVE_SOP: bool = False             # allow LLM to propose step/SOP deviations
    LLM_NUM_CTX: int = 8192                    # context window size (tokens)

    # Tracing
    TRACE_ENABLED: bool = False                # set TRACE_ENABLED=true to emit TRACE-level logs

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
