import asyncio
import json

from fastapi import WebSocket


# Per-send timeout for WebSocket broadcasts.  If a client's TCP send
# buffer is full (browser tab dead, network blip, slow consumer), the
# send would otherwise block the broadcast loop indefinitely while the
# kernel waits for TCP retransmission timeouts (minutes).  Two seconds
# is generous for healthy clients on a LAN and short enough that one
# dead client can't freeze the whole broadcaster.
_BROADCAST_SEND_TIMEOUT_SECONDS = 2.0


class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, message_type: str, data: dict):
        msg = json.dumps({"type": message_type, "data": data}, default=str)
        disconnected = []
        for ws in list(self.connections):
            try:
                await asyncio.wait_for(
                    ws.send_text(msg),
                    timeout=_BROADCAST_SEND_TIMEOUT_SECONDS,
                )
            except (asyncio.TimeoutError, Exception):
                disconnected.append(ws)
        for ws in disconnected:
            if ws in self.connections:
                self.connections.remove(ws)
            try:
                await ws.close()
            except Exception:
                pass
