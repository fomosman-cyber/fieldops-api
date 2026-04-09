from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from database import get_db
from models import User
from schemas import LoginRequest, TokenResponse, UserResponse
from auth import verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["Authenticatie"])


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Onjuist e-mailadres of wachtwoord")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is gedeactiveerd")

    # Check of organisatie nog actief is
    org = user.organization
    if org.status == "expired":
        raise HTTPException(status_code=403, detail="Uw proefperiode is verlopen. Neem een abonnement.")
    if org.status == "suspended":
        raise HTTPException(status_code=403, detail="Account is opgeschort. Neem contact op met support.")

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(data={"sub": user.id, "org": user.organization_id, "role": user.role.value})

    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
