from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
from models import User
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

if not SECRET_KEY or SECRET_KEY in ("fieldops-secret-key", "fieldops-secret-key-change-in-production-2024"):
    if os.getenv("RENDER") or os.getenv("ENV") == "production":
        raise RuntimeError("SECRET_KEY env variabele moet gezet zijn in productie")
    SECRET_KEY = "dev-only-not-for-production"
    print("[WARN] SECRET_KEY niet gezet — dev fallback actief")

security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Ongeldige token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Ongeldige token")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Gebruiker niet gevonden")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is gedeactiveerd")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Admin rechten vereist")
    return current_user
