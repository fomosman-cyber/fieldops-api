"""Realtime broadcast laag — push events naar verbonden clients via WebSocket.

Eén globale `EventBus` houdt actieve websocket-verbindingen per organisatie bij.
Wanneer `broadcast_event()` wordt aangeroepen vanuit `audit.log_action()` na een
geslaagde event-fanout, krijgen alle verbonden clients van dezelfde organisatie
de payload binnen ~10ms.

Auth via JWT in de query: `?token=<JWT>`. WebSocket-protocol heeft geen
Authorization-header; query-token is gangbaar en de token bevat geen secret
voorbij wat de gebruiker al heeft.

Schaalbaarheid: dit werkt voor één-proces deployments (Render free / hobby).
Voor multi-instance is een Redis pub/sub-laag nodig — komt later wanneer
volume het rechtvaardigt.
"""

from __future__ import annotations
import asyncio
import json
from typing import Optional
from collections import defaultdict

from fastapi import WebSocket


class EventBus:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, org_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[org_id].add(ws)

    async def disconnect(self, org_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.get(org_id, set()).discard(ws)

    async def broadcast(self, org_id: str, payload: dict) -> None:
        # Snapshot om iteratie buiten de lock te doen (en dead conns op te ruimen)
        async with self._lock:
            conns = list(self._connections.get(org_id, set()))
        if not conns:
            return
        text = json.dumps(payload, default=str, ensure_ascii=False)
        dead: list[WebSocket] = []
        for c in conns:
            try:
                await c.send_text(text)
            except Exception:
                dead.append(c)
        if dead:
            async with self._lock:
                pool = self._connections.get(org_id)
                if pool:
                    for d in dead:
                        pool.discard(d)


bus = EventBus()


def broadcast_event(action: str, organization_id: Optional[str], payload: dict) -> None:
    """Synchrone wrapper — gebruikt vanuit `audit.log_action()`.

    De caller zit waarschijnlijk in een sync request-context; we schedulen de
    broadcast op de huidige event loop. Geen exceptions doorzetten."""
    if not organization_id:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(bus.broadcast(
                organization_id, {"event_type": action, **payload}
            ))
        else:
            asyncio.run(bus.broadcast(
                organization_id, {"event_type": action, **payload}
            ))
    except RuntimeError:
        # Geen actieve loop (bv. CLI / migratie-context) — geen broadcast
        pass
    except Exception as e:
        print(f"[realtime] broadcast '{action}' faalde: {e}")
