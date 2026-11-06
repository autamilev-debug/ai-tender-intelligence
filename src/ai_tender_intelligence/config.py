"""Load only the foundation's non-secret process configuration."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigurationError(ValueError):
    """Invalid local configuration, without reflecting supplied values."""


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "local"

    def __post_init__(self) -> None:
        if self.environment not in {"local", "test"}:
            raise ConfigurationError("APP_ENV must be local or test.")


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read APP_ENV only; never load dotenv files or integration credentials."""
    source = os.environ if environ is None else environ
    return Settings(environment=source.get("APP_ENV", "local"))
