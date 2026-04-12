from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    llm_api_key: str = ""
    llm_base_url: str = "https://api.minimax.chat/v1"
    llm_model: str = "MiniMax-M2.7"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Agent 超时（秒）
    agent_timeout: float = 10.0

    model_config = {
        "env_prefix": "ECOM_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
