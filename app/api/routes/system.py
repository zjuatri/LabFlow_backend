from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def root():
    return {"message": "Welcome to LabFlow Backend"}
