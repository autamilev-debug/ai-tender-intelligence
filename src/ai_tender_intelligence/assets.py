"""Locate versioned prompts, migrations and official reference data portably."""
from pathlib import Path
import sys


def root():
    source=Path(__file__).resolve().parents[2]
    if (source/'pyproject.toml').is_file() and (source/'prompts').is_dir():return source
    installed=Path(sys.prefix)/'share/ai-tender-intelligence'
    if installed.is_dir():return installed
    raise RuntimeError('Application assets are missing; install from the complete source distribution.')
