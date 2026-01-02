from datetime import datetime, timezone
import shutil

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...defaults import DEFAULT_TYPST_CODE
from ...models import Project, User
from ...schemas import ProjectCreateRequest, ProjectResponse, ProjectUpdateRequest
from ...security import get_current_user

from .typst import _project_storage_dir, cleanup_unused_images

router = APIRouter()


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Project).where(Project.user_id == current_user.id)
    if type:
        stmt = stmt.where(Project.type == type)
    stmt = stmt.order_by(Project.updated_at.desc())
    return list(db.scalars(stmt).all())


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    project = Project(
        user_id=current_user.id,
        title=payload.title,
        type=payload.type,
        typst_code=DEFAULT_TYPST_CODE,
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Backfill legacy projects that were created with empty typst_code.
    if not (project.typst_code or "").strip():
        project.typst_code = DEFAULT_TYPST_CODE
        project.updated_at = datetime.now(timezone.utc)
        db.add(project)
        db.commit()
        db.refresh(project)
    return project


@router.put("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    if payload.title is not None:
        project.title = payload.title
    if payload.type is not None:
        project.type = payload.type
    if payload.typst_code is not None:
        project.typst_code = payload.typst_code

    project.updated_at = datetime.now(timezone.utc)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    try:
        shutil.rmtree(_project_storage_dir(project_id), ignore_errors=True)
    except Exception:
        pass
    return Response(status_code=204)
