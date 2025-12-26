from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...schemas import LoginRequest, RegisterRequest, TokenResponse
from ...security import create_access_token, hash_password, verify_password

router = APIRouter()


@router.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=400, detail="Email already registered (该邮箱已注册)")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered (该邮箱已注册)")
    db.refresh(user)

    token = create_access_token(subject=user.id, role=user.role)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(subject=user.id, role=user.role)
    return TokenResponse(access_token=token)
