from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

# 环境变量文件路径可被 ENV_FILE 覆盖；密钥只放环境变量与本地 secrets/ 目录，不放 yaml。
_DEFAULT_ENV_FILE = ".env"
_DEFAULT_SECRETS_FILE = "secrets/llm.env"
_DEFAULT_CONFIG = "config/app.yaml"


class Settings(BaseSettings):
    """运行时配置：默认值 < config/app.yaml < .env < secrets/llm.env < 进程环境变量。"""

    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=(_DEFAULT_ENV_FILE, _DEFAULT_SECRETS_FILE),
        case_sensitive=False,
    )

    app_name: str = "sixfoot-route-agent"
    app_env: str = "local"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    hard_deadline_seconds: int = 60
    max_candidates: int = 50
    top_k_evidence: int = 5
    max_repair_rounds: int = 2

    worker_enabled: bool = False
    worker_concurrency: int = 20
    lease_seconds: int = 30
    heartbeat_seconds: int = 10

    search_provider: str = "fake"
    search_timeout_seconds: float = 2.0
    search_max_page_size: int = 50
    search_reserved_concurrency: int = 5

    evidence_batch_size: int = 20
    evidence_timeout_seconds: float = 3.0

    max_input_chars: int = 4000
    max_in_flight_runs_per_user: int = 3
    max_facts_per_run: int = 30
    max_provider_calls_per_run: int = 20

    mysql_dsn: str = "mysql+asyncmy://route_agent:route_agent@127.0.0.1:3307/route_agent"
    redis_url: str = "redis://127.0.0.1:6380/0"
    elasticsearch_url: str = "http://127.0.0.1:9201"
    elasticsearch_index: str = "route_agent_routes"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_fallback_model: str = ""
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 2048


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _flatten_sections(data: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for value in data.values():
        if isinstance(value, dict):
            flat.update(value)
    return flat


def _explicit_env_keys(env_file: Path, secrets_file: Path) -> set[str]:
    keys = {key.lower() for key in os.environ}
    for path in (env_file, secrets_file):
        if path.is_file():
            keys |= {key.lower() for key in dotenv_values(path)}
    return keys


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_path = Path(os.environ.get("APP_CONFIG", _DEFAULT_CONFIG))
    env_file = Path(os.environ.get("ENV_FILE", _DEFAULT_ENV_FILE))
    secrets_file = Path(os.environ.get("SECRETS_FILE", _DEFAULT_SECRETS_FILE))
    yaml_overrides = _flatten_sections(_load_yaml(config_path))
    # 显式设置的环境变量与本地 secrets 优先于 yaml，因此只覆盖未被显式给出的键。
    explicit = _explicit_env_keys(env_file, secrets_file)
    updates = {key: value for key, value in yaml_overrides.items() if key.lower() not in explicit}
    return Settings(**updates)
