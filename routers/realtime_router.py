"""WebSocket-endpoint voor realtime event-feed.

Connect met: ws://host/api/ws/events?token=<JWT>

Server stuurt direct na verbinden een `{"event_type": "ws.connected"}` ack;
daarna pushed elke audit-actie binnen jouw organisatie automatisch."""

import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError, jwt

from auth import SECRET_KEY, ALGORITHM
from database import SessionLocal
from models import User
from realtime import bus

router = APIRouter(tags=["Realtime"])


def _user_from_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user if user and user.is_active else None
    finally:
        db.close()


@router.websocket("/api/ws/events")
async def ws_events(websocket: WebSocket, token: str = Query("")):
    user = _user_from_token(token)
    if not user:
        await websocket.close(code=4401, reason="invalid token")
        return

    await websocket.accept()
    org_id = user.organization_id
    await bus.connect(org_id, websocket)
    try:
        await websocket.send_text(json.dumps({
            "event_type": "ws.connected",
            "user_email": user.email,
            "organization_id": org_id,
        }))
        # Houd open — client mag pingen, server stuurt push uit broadcast
        while True:
            msg = await websocket.receive_text()
            # Echo "ping" → "pong" zodat clients keep-alive simpel kunnen doen
            try:
                obj = json.loads(msg)
            except json.JSONDecodeError:
                obj = {"raw": msg[:200]}
            if obj.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[realtime] ws-error: {e}")
    finally:
        await bus.disconnect(org_id, websocket)
