from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Document, User, SystemConfig
from ...schemas import DocumentCreate, DocumentResponse, DocumentUpdate, SidebarStructureUpdate
from ...security import get_current_user

router = APIRouter()

SIDEBAR_CONFIG_KEY = "docs_sidebar"

@router.get("/docs/structure")
def get_sidebar_structure(db: Session = Depends(get_db)):
    config = db.scalar(select(SystemConfig).where(SystemConfig.key == SIDEBAR_CONFIG_KEY))
    if not config:
        # Default structure: All docs flat or empty?
        # Let's return empty list or a default structure
        return []
    
    import json
    try:
        return json.loads(config.value)
    except:
        return []


@router.put("/docs/structure")
def update_sidebar_structure(
    payload: SidebarStructureUpdate, 
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    structure = payload.structure
    import json
    config = db.scalar(select(SystemConfig).where(SystemConfig.key == SIDEBAR_CONFIG_KEY))
    if not config:
        config = SystemConfig(key=SIDEBAR_CONFIG_KEY, value=json.dumps(structure))
        db.add(config)
    else:
        config.value = json.dumps(structure)
    
    db.commit()
    return structure


@router.get("/docs", response_model=list[DocumentResponse])
def list_documents(
    published_only: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user), # Optional: Relax this if public access is needed without login
):
    # Note: If we want public access without login, we should make current_user optional.
    # However, for now, let's assume public docs might be accessible without auth?
    # The requirement imply this is for "Docs Site".
    # Let's verify if `get_current_user` raises or returns None. 
    # Usually `get_current_user` raises 401. 
    # For a public doc site, we might need a separate endpoint or optional auth.
    # Let's split: /docs (public) and /manage/docs (private). 
    # But usually REST APIs are: GET /docs (public/all), POST /docs (admin).
    
    # For simplicity in this iteration, we keep it simple. 
    # If published_only is True, we return published docs.
    # If generic user (or no user), return published.
    # If admin wants to see all, they can filter?
    
    # Let's Remove strict Auth for LIST GET if published_only=True
    stmt = select(Document).order_by(Document.updated_at.desc())
    if published_only:
        stmt = stmt.where(Document.is_published == True)
        
    return list(db.scalars(stmt).all())


@router.get("/docs/{slug}", response_model=DocumentResponse)
def get_document(
    slug: str,
    db: Session = Depends(get_db),
):
    stmt = select(Document).where(Document.slug == slug)
    doc = db.scalar(stmt)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # If not published, maybe hide? For now return it.
    return doc


@router.post("/docs", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreate,
    current_user: User = Depends(get_current_user), # Require auth for mutations
    db: Session = Depends(get_db),
):
    # Check slug uniqueness
    existing = db.scalar(select(Document).where(Document.slug == payload.slug))
    if existing:
        raise HTTPException(status_code=400, detail="Slug already exists")

    now = datetime.now(timezone.utc)
    doc = Document(
        id=str(uuid.uuid4()),
        slug=payload.slug,
        title=payload.title,
        content=payload.content,
        is_published=payload.is_published,
        created_at=now,
        updated_at=now,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.put("/docs/{doc_id}", response_model=DocumentResponse)
def update_document(
    doc_id: str,
    payload: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if payload.slug is not None:
        # Check uniqueness if slug changed
        if payload.slug != doc.slug:
            existing = db.scalar(select(Document).where(Document.slug == payload.slug))
            if existing:
                raise HTTPException(status_code=400, detail="Slug already exists")
        doc.slug = payload.slug

    if payload.title is not None:
        doc.title = payload.title
    if payload.content is not None:
        doc.content = payload.content
    if payload.is_published is not None:
        doc.is_published = payload.is_published

    doc.updated_at = datetime.now(timezone.utc)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/docs/{doc_id}")
def delete_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    db.delete(doc)
    db.commit()
    return Response(status_code=204)
