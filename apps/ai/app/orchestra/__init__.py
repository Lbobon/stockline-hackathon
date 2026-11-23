"""The call orchestra: agents that work around the voice agent during a call (see session.py), or around Leïla (copilot.py)."""

from .copilot import CopilotSession
from .session import CallSession, FastEvent, FillerEvent

__all__ = ["CallSession", "CopilotSession", "FastEvent", "FillerEvent"]
