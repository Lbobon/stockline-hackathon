"""WS /voice: push-to-talk voice sessions (protocol: app/schemas/voice.py, logic: app/services/voice_session.py).
WS /voice/command: one voice command with live transcription (app/services/command.py, serve_command_ws).
WS /voice/phone, /voice/watch: the demo phone line (app/services/phone_line.py)."""

from fastapi import APIRouter, WebSocket

from app.services.command import serve_command_ws
from app.services.phone_line import serve_phone, serve_watch
from app.services.voice_session import VoiceConnection

router = APIRouter()


@router.websocket("/voice")
async def voice(ws: WebSocket, line: str | None = None):
    # line=phone: the session of the demo phone, mirrored live to the PCs watching (/voice/watch)
    await VoiceConnection(ws, mirror=line == "phone").serve()


@router.websocket("/voice/command")
async def voice_command(ws: WebSocket, speak: str | None = None):
    await serve_command_ws(ws, speak=speak in ("1", "true"))


@router.websocket("/voice/phone")
async def phone(ws: WebSocket):
    await serve_phone(ws)


@router.websocket("/voice/watch")
async def watch(ws: WebSocket):
    await serve_watch(ws)
