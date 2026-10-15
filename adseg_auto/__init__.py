"""Auditable local TV broadcast segmentation. No inference runs on import."""

__version__ = "1.0.0"

from .pipeline import Config, run
from .providers import Provider, ReplayProvider, HTTPProvider, OfflineProvider

__all__ = ["Config", "run", "Provider", "ReplayProvider", "HTTPProvider", "OfflineProvider"]
