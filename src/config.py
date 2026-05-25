"""
Centralized configuration loader.

Reads `config.yaml` for static defaults and `.env` for secrets/paths.
Anything you want to be runtime-tunable should live in `config.yaml`;
anything secret or environment-specific should live in `.env`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Locate the project root reliably regardless of where the CLI was invoked.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config.yaml"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


# ---------------------------------------------------------------------------
# Typed config containers. Plain dataclasses, no Pydantic dependency here
# so config loading stays cheap and import-time safe.
# ---------------------------------------------------------------------------
@dataclass
class LLMTaskConfig:
    provider: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024


@dataclass
class LLMConfig:
    chat: LLMTaskConfig
    reasoning: LLMTaskConfig
    vision: LLMTaskConfig
    fallback: dict[str, str | None] = field(default_factory=dict)
    budget_cny_monthly: dict[str, float] = field(default_factory=dict)


@dataclass
class StorageConfig:
    data_dir: Path
    sqlite_path: Path
    pdf_dir: Path
    chroma_dir: Path
    chroma_collection: str


@dataclass
class CrawlerConfig:
    base_url: str
    start_paths: list[str]
    request_timeout: int
    retries: int
    category_whitelist: list[str]
    skip_keywords: list[str]
    delay_seconds: float = 1.5
    user_agent: str = "UNIS-Product-Selection/0.1"


@dataclass
class SelectorConfig:
    default_mode: str       # "rule" | "ai"
    top_k: int
    ai_context_top_n: int


@dataclass
class SchedulerConfig:
    enabled: bool
    crawl_cron: str
    timezone: str


@dataclass
class LoggingConfig:
    level: str
    file: Path | None


@dataclass
class Secrets:
    """API keys + per-deploy overrides loaded from `.env`."""

    deepseek_api_key: str | None
    deepseek_base_url: str
    anthropic_api_key: str | None


@dataclass
class AppConfig:
    llm: LLMConfig
    storage: StorageConfig
    crawler: CrawlerConfig
    selector: SelectorConfig
    scheduler: SchedulerConfig
    logging: LoggingConfig
    secrets: Secrets

    # --- convenience helpers --------------------------------------------------
    def task(self, name: str) -> LLMTaskConfig:
        """Get config for a named LLM task (`chat`, `reasoning`, `vision`)."""
        try:
            return getattr(self.llm, name)
        except AttributeError as exc:
            raise KeyError(f"Unknown LLM task: {name!r}") from exc

    def ensure_dirs(self) -> None:
        """Create data dirs at startup so downstream code can assume they exist."""
        for p in (
            self.storage.data_dir,
            self.storage.pdf_dir,
            self.storage.chroma_dir,
            self.storage.sqlite_path.parent,
        ):
            p.mkdir(parents=True, exist_ok=True)
        if self.logging.file:
            self.logging.file.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def _abs(p: str | Path) -> Path:
    """Resolve a path relative to project root if it's not absolute."""
    path = Path(p)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            f"Copy `config.yaml` from the repo root or set CONFIG_FILE env var."
        )
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build(raw: dict[str, Any]) -> AppConfig:
    llm_raw = raw["llm"]
    llm = LLMConfig(
        chat=LLMTaskConfig(**llm_raw["chat"]),
        reasoning=LLMTaskConfig(**llm_raw["reasoning"]),
        vision=LLMTaskConfig(**llm_raw["vision"]),
        fallback=llm_raw.get("fallback", {}),
        budget_cny_monthly=llm_raw.get("budget_cny_monthly", {}),
    )

    s = raw["storage"]
    storage = StorageConfig(
        data_dir=_abs(s["data_dir"]),
        sqlite_path=_abs(s["sqlite_path"]),
        pdf_dir=_abs(s["pdf_dir"]),
        chroma_dir=_abs(s["chroma_dir"]),
        chroma_collection=s["chroma_collection"],
    )

    c = raw["crawler"]
    crawler = CrawlerConfig(
        base_url=c["base_url"],
        start_paths=c.get("start_paths", []),
        request_timeout=c.get("request_timeout", 30),
        retries=c.get("retries", 3),
        category_whitelist=c.get("category_whitelist", []),
        skip_keywords=c.get("skip_keywords", []),
        delay_seconds=float(os.getenv("CRAWLER_DELAY", "1.5")),
        user_agent=os.getenv("CRAWLER_USER_AGENT", "UNIS-Product-Selection/0.1"),
    )

    sel = raw["selector"]
    selector = SelectorConfig(
        default_mode=sel.get("default_mode", "rule"),
        top_k=sel.get("top_k", 5),
        ai_context_top_n=sel.get("ai_context_top_n", 20),
    )

    sc = raw["scheduler"]
    scheduler = SchedulerConfig(
        enabled=sc.get("enabled", False),
        crawl_cron=sc.get("crawl_cron", "0 3 * * 1"),
        timezone=sc.get("timezone", "Asia/Shanghai"),
    )

    lg = raw.get("logging", {})
    log_file = lg.get("file")
    logging_cfg = LoggingConfig(
        level=lg.get("level", "INFO"),
        file=_abs(log_file) if log_file else None,
    )

    secrets = Secrets(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
    )

    return AppConfig(
        llm=llm,
        storage=storage,
        crawler=crawler,
        selector=selector,
        scheduler=scheduler,
        logging=logging_cfg,
        secrets=secrets,
    )


@lru_cache(maxsize=1)
def get_config(config_file: str | os.PathLike | None = None) -> AppConfig:
    """
    Load and cache the application config.

    Call sites should treat the returned object as read-only. To reload at
    runtime (e.g. tests), call `get_config.cache_clear()` first.
    """
    # Load .env BEFORE reading the YAML so env-driven overrides apply.
    load_dotenv(DEFAULT_ENV_FILE, override=False)

    path = Path(config_file) if config_file else Path(os.getenv("CONFIG_FILE", DEFAULT_CONFIG_FILE))
    raw = _read_yaml(path)
    cfg = _build(raw)
    cfg.ensure_dirs()
    return cfg


__all__ = [
    "AppConfig",
    "LLMConfig",
    "LLMTaskConfig",
    "StorageConfig",
    "CrawlerConfig",
    "SelectorConfig",
    "SchedulerConfig",
    "LoggingConfig",
    "Secrets",
    "PROJECT_ROOT",
    "get_config",
]
