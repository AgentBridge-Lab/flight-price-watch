from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

DEFAULT_ENV_PATH = Path.home() / ".config" / "flight-price-watch" / ".env"
FLIGHT_ENV_PATH = Path(os.environ.get("FLIGHT_WATCH_ENV_FILE", DEFAULT_ENV_PATH))

# 다른 곳에 있는 .env 를 쓰려면 FLIGHT_WATCH_ENV_FILE 환경변수나 --secrets-path 를 쓴다.
DEFAULT_ENV_CANDIDATES = (
    FLIGHT_ENV_PATH,
    Path.cwd() / ".env",
)

PLACEHOLDER_PREFIXES = ("your_", "YOUR_")
PLACEHOLDER_SUBSTRINGS = ("_here", "...", "your-")


def is_placeholder_value(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith(PLACEHOLDER_PREFIXES) or any(token in stripped for token in PLACEHOLDER_SUBSTRINGS)


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r", "")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if is_placeholder_value(value):
            continue
        env[key] = value
    return env


def load_env_candidates(paths: Iterable[Path], *, override: bool = False) -> list[Path]:
    loaded: list[Path] = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        for key, value in parse_env_file(path).items():
            if override:
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)
        loaded.append(path)
    return loaded


def default_env_candidates() -> tuple[Path, ...]:
    return DEFAULT_ENV_CANDIDATES
