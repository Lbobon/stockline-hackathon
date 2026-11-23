"""Outbound call campaigns run in parallel (radar de demande). See engine.py."""

from .engine import CAMPAIGNS, start

__all__ = ["CAMPAIGNS", "start"]
