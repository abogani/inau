"""
INAU Webhook Handler
Gestisce i webhook da GitLab per trigger di nuove build su tag annotati
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict
from sqlmodel import SQLModel, Session, create_engine, select
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
import logging
import json
from contextlib import asynccontextmanager
import os
from celery import Celery

# Import dei modelli condivisi
from models import (
    Architecture, Distribution, Platform, Provider, Repository,
    Build, Artifact, Builder, Server, Facility, Host, User, Installation,
    RepositoryType, BuildStatus, InstallationType
)

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurazione database
DATABASE_URL = os.getenv('DATABASE_URL', None)
engine = create_engine(DATABASE_URL, echo=False)

# Setup Celery
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', None)
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', None)
celery_app = Celery('inau_webhook', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

# Modelli Pydantic per i webhook

class GitLabProject(BaseModel):
    """Progetto GitLab dal webhook"""
    id: int
    name: str
    description: Optional[str]
    web_url: str
    git_ssh_url: str
    git_http_url: str
    namespace: str
    path_with_namespace: str
    default_branch: str
    ssh_url: str
    http_url: str

class GitLabUser(BaseModel):
    """Utente GitLab che ha fatto il push"""
    id: int
    name: str
    username: str
    email: str
    avatar: Optional[str]

class GitLabCommitAuthor(BaseModel):
    """Autore del commit"""
    name: str
    email: str

class GitLabCommit(BaseModel):
    """Commit nel push"""
    id: str
    message: str
    title: str
    timestamp: str
    url: str
    author: GitLabCommitAuthor
    added: List[str]
    modified: List[str]
    removed: List[str]

class GitLabWebhook(BaseModel):
    """Payload del webhook GitLab per tag push"""
    model_config = ConfigDict(extra='allow')
    
    object_kind: str
    event_name: str
    before: str
    after: str
    ref: str
    checkout_sha: str
    message: Optional[str]
    user_id: int
    user_name: str
    user_username: str
    user_email: str
    user_avatar: Optional[str]
    project_id: int
    project: GitLabProject
    commits: List[GitLabCommit]
    total_commits_count: int
    repository: dict

# FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestione del ciclo di vita dell'applicazione"""
    # Startup
    logger.info("Starting INAU Webhook Handler...")
    SQLModel.metadata.create_all(engine)
    yield
    # Shutdown
    logger.info("Shutting down INAU Webhook Handler...")

app = FastAPI(
    title="INAU Webhook Handler", 
    version="1.0.0",
    lifespan=lifespan
)

# Dependency per ottenere la sessione del database
def get_session():
    with Session(engine) as session:
        yield session

# Funzioni di utilità

def extract_tag_from_ref(ref: str) -> Optional[str]:
    """Estrae il nome del tag dal ref GitLab"""
    if ref.startswith("refs/tags/"):
        return ref.replace("refs/tags/", "")
    return None

def find_repositories(session: Session, project_path: str) -> List[Repository]:
    """Trova tutti i repository abilitati per il progetto"""
    return session.exec(
        select(Repository).where(
            Repository.name == project_path,
            Repository.enabled == True
        )
    ).all()

def schedule_builds(
    session: Session, 
    repositories: List[Repository], 
    tag: str, 
    webhook: GitLabWebhook
) -> List[Build]:
    """Schedula le build per tutte le piattaforme abilitate dei repository"""
    builds = []
    
    for repository in repositories:
        # Verifica se esiste già una build per questo tag e piattaforma
        existing_build = session.exec(
            select(Build).where(
                Build.repository_id == repository.id,
                Build.platform_id == repository.platform_id,
                Build.tag == tag
            )
        ).first()
        
        if not existing_build:
            build = Build(
                repository_id=repository.id,
                platform_id=repository.platform_id,
                tag=tag,
                status=BuildStatus.SCHEDULED
            )
            session.add(build)
            session.commit()
            session.refresh(build)
            
            # Prepara i dati per Celery
            build_task = {
                "build_id": build.id,
                "repository_id": repository.id,
                "platform_id": repository.platform_id,
                "tag": tag,
                "repository_name": repository.name,
                "repository_url": webhook.project.ssh_url,
                "repository_type": repository.type,
                "user_email": webhook.commits[0].author.email if webhook.commits else webhook.user_email,
                "default_branch": webhook.project.default_branch,
                # Email multiple per compatibilità con vecchio sistema
                "emails": [
                    webhook.commits[0].author.email if webhook.commits else None,
                    f"{webhook.user_username}@elettra.eu",
                    webhook.user_email
                ]
            }
            
            # Invia il task a Celery
            notify_celery_worker(build_task)
            builds.append(build)
    
    return builds

def notify_celery_worker(build_task: dict):
    """Invia il task di build al worker Celery"""
    try:
        # Invia il task usando send_task per evitare import circolari
        result = celery_app.send_task(
            'inau.build.process_build',
            args=[build_task],
            queue='build_queue'
        )
        logger.info(f"Build task sent to Celery: {build_task['build_id']}, task_id: {result.id}")
    except Exception as e:
        logger.error(f"Failed to send build task to Celery: {str(e)}")

# Endpoints

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    health = {
        "status": "ok",
        "service": "inau-webhook",
        "checks": {}
    }
    
    # Check database
    try:
        with Session(engine) as session:
            session.exec(select(1))
        health["checks"]["database"] = "ok"
    except Exception as e:
        health["checks"]["database"] = f"error: {str(e)}"
        health["status"] = "degraded"
    
    # Check Celery broker
    try:
        celery_app.control.inspect().stats()
        health["checks"]["celery_broker"] = "ok"
    except Exception as e:
        health["checks"]["celery_broker"] = f"error: {str(e)}"
        health["status"] = "degraded"
    
    return health

@app.post("/")
async def handle_gitlab_webhook(
    webhook: GitLabWebhook,
    session: Session = Depends(get_session)
):
    """
    Gestisce i webhook di GitLab per i tag push
    """
    try:
        # Verifica che sia un tag push
        if webhook.object_kind != "tag_push":
            return JSONResponse(
                status_code=200,
                content={"message": "Ignored: not a tag push event"}
            )
        
        # Ignora cancellazione di tag
        if webhook.after == '0000000000000000000000000000000000000000':
            return JSONResponse(
                status_code=200,
                content={"message": "Ignored: tag deletion"}
            )
        
        # Estrai il tag dal ref
        tag = extract_tag_from_ref(webhook.ref)
        if not tag:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid tag reference"}
            )
        
        # Verifica che non sia un tag lightweight (after == commit id)
        if webhook.commits and webhook.after == webhook.commits[0].id:
            return JSONResponse(
                status_code=200,
                content={"message": "Ignored: lightweight tag"}
            )
        
        logger.info(f"Received tag push: {tag} for project {webhook.project.path_with_namespace}")
        
        # Trova tutti i repository configurati per questo progetto
        repositories = find_repositories(session, webhook.project.path_with_namespace)
        
        if not repositories:
            logger.warning(f"Repository {webhook.project.path_with_namespace} not found or not enabled")
            return JSONResponse(
                status_code=200,
                content={"message": f"Repository {webhook.project.path_with_namespace} not configured for builds"}
            )
        
        # Schedula le build per tutte le piattaforme abilitate
        builds = schedule_builds(session, repositories, tag, webhook)
        
        return JSONResponse(
            status_code=201,
            content={
                "message": f"Scheduled {len(builds)} builds for tag {tag}",
                "builds": [{"id": b.id, "platform_id": b.platform_id} for b in builds]
            }
        )
        
    except Exception as e:
        logger.error(f"Error handling webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
