"""Runtime settings, read once from environment variables (or a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader so the app runs without extra dependencies."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# Approximate list prices (USD per 1M tokens) used only for the on-screen cost estimate.
DEFAULT_PRICES = {
    "fast": (0.06, 0.24),    # Nemotron 3 Nano
    "judge": (0.30, 0.90),   # Nemotron 3 Super
    "reason": (1.00, 3.00),  # Nemotron 3 Ultra
}


@dataclass
class Settings:
    nebius_api_key: str = field(default_factory=lambda: os.getenv("NEBIUS_API_KEY", ""))
    nebius_base_url: str = field(
        default_factory=lambda: os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
    )
    # Three Nemotron models, each with one job (see README "Which model does what").
    model_fast: str = field(default_factory=lambda: os.getenv("MODEL_FAST", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"))
    model_judge: str = field(default_factory=lambda: os.getenv("MODEL_JUDGE", "nvidia/nemotron-3-super-120b-a12b"))
    model_reason: str = field(default_factory=lambda: os.getenv("MODEL_REASON", "nvidia/Nemotron-3-Ultra-550b-a55b"))
    fast_thinking: bool = field(default_factory=lambda: _bool("FAST_MODEL_THINKING", False))

    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))

    mock: bool = field(default_factory=lambda: _bool("RECEIPTS_MOCK", False))
    max_chars: int = field(default_factory=lambda: _int("MAX_DOC_CHARS", 40000))
    max_claims: int = field(default_factory=lambda: _int("MAX_CLAIMS", 30))
    concurrency: int = field(default_factory=lambda: _int("CLAIM_CONCURRENCY", 5))
    escalate_below: float = field(default_factory=lambda: _float("ESCALATE_BELOW_CONFIDENCE", 0.75))
    live_runs_per_hour: int = field(default_factory=lambda: _int("LIVE_RUNS_PER_HOUR", 12))
    live_runs_per_day: int = field(default_factory=lambda: _int("LIVE_RUNS_PER_DAY", 300))
    replay_delay: float = field(default_factory=lambda: _float("REPLAY_DELAY_SECONDS", 0.18))
    cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("CACHE_DIR", Path(__file__).resolve().parent.parent / "data" / "cache"))
    )

    def model_for(self, role: str) -> str:
        return {"fast": self.model_fast, "judge": self.model_judge, "reason": self.model_reason}[role]

    @property
    def live_ready(self) -> bool:
        return bool(self.nebius_api_key and self.tavily_api_key)


settings = Settings()
