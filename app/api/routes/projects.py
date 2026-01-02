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
    import uuid
    import re
    project_id = str(uuid.uuid4())
    typst_code = DEFAULT_TYPST_CODE
    
    # Collect all referenced project IDs from source typst_code
    referenced_project_ids: set[str] = set()

    if payload.source_project_id:
        source = db.get(Project, payload.source_project_id)
        if not source or source.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Source project not found")
        
        # Find all project IDs referenced in the typst code (images/charts paths)
        # Pattern matches UUIDs in paths like /static/projects/<uuid>/ or projects/<uuid>/
        uuid_pattern = r'projects/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/'
        referenced_project_ids = set(re.findall(uuid_pattern, source.typst_code, re.IGNORECASE))
        
        # Replace ALL project IDs in paths with new project ID
        # Pattern 1: /static/projects/<uuid>/images/ or /charts/ (absolute paths)
        typst_code = re.sub(
            r'/static/projects/[0-9a-f-]+/images/',
            f'/static/projects/{project_id}/images/',
            source.typst_code,
            flags=re.IGNORECASE
        )
        typst_code = re.sub(
            r'/static/projects/[0-9a-f-]+/charts/',
            f'/static/projects/{project_id}/charts/',
            typst_code,
            flags=re.IGNORECASE
        )
        
        # Pattern 2: projects/<uuid>/images/ or /charts/ (relative paths without /static/)
        typst_code = re.sub(
            r'(?<!/static/)projects/[0-9a-f-]+/images/',
            f'projects/{project_id}/images/',
            typst_code,
            flags=re.IGNORECASE
        )
        typst_code = re.sub(
            r'(?<!/static/)projects/[0-9a-f-]+/charts/',
            f'projects/{project_id}/charts/',
            typst_code,
            flags=re.IGNORECASE
        )

    now = datetime.now(timezone.utc)
    project = Project(
        id=project_id,
        user_id=current_user.id,
        title=payload.title,
        type=payload.type,
        typst_code=typst_code,
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    if payload.source_project_id:
        dst_dir = _project_storage_dir(project.id)
        
        # Copy files from ALL referenced projects (not just the source)
        # This handles cases where source project references images from other projects
        for ref_id in referenced_project_ids:
            ref_dir = _project_storage_dir(ref_id)
            if ref_dir.exists():
                shutil.copytree(ref_dir, dst_dir, dirs_exist_ok=True)

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
