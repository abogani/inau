"""
INAU Webhook Handler
Gestisce i webhook da GitLab per trigger di nuove build su tag annotati
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict
from sqlmodel import Field, SQLModel, Session, create_engine, select, Relationship
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
import logging
from enum import IntEnum
import json
from contextlib import asynccontextmanager

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurazione database
DATABASE_URL = "postgresql://inau:Inau123@localhost/inau"
engine = create_engine(DATABASE_URL, echo=False)

# Enum per i tipi
# TODO Controllare
class RepositoryType(IntEnum):
    """Tipi di repository supportati"""
    GITLAB = 1
    GITHUB = 2
    BITBUCKET = 3

# TODO Controllare
class BuildStatus(IntEnum):
    """Stati possibili per una build"""
    SCHEDULED = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    CANCELLED = 4

# TODO Controllare
class InstallationType(IntEnum):
    """Tipi di installazione"""
    PRODUCTION = 1
    STAGING = 2
    DEVELOPMENT = 3
    TEST = 4

# Modelli SQLModel

class Architecture(SQLModel, table=True):
    """Architetture supportate"""
    __tablename__ = "architectures"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=255)
    
    # Relationships
    platforms: List["Platform"] = Relationship(back_populates="architecture")

class Distribution(SQLModel, table=True):
    """Distribuzioni supportate"""
    __tablename__ = "distributions"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    version: str = Field(max_length=255)
    
    # Relationships
    platforms: List["Platform"] = Relationship(back_populates="distribution")
    
    class Config:
        # Unique constraint on (name, version)
        table_args = (
            {"unique_together": [("name", "version")]},
        )

class Platform(SQLModel, table=True):
    """Piattaforme di build (combinazione di distribuzione + architettura)"""
    __tablename__ = "platforms"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    distribution_id: int = Field(foreign_key="distributions.id", index=True)
    architecture_id: int = Field(foreign_key="architectures.id", index=True)
    
    # Relationships
    distribution: Distribution = Relationship(back_populates="platforms")
    architecture: Architecture = Relationship(back_populates="platforms")
    repositories: List["Repository"] = Relationship(back_populates="platform")
    servers: List["Server"] = Relationship(back_populates="platform")
    hosts: List["Host"] = Relationship(back_populates="platform")

class Provider(SQLModel, table=True):
    """Provider di repository"""
    __tablename__ = "providers"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True, index=True, max_length=255)
    
    # Relationships
    repositories: List["Repository"] = Relationship(back_populates="provider")

class Repository(SQLModel, table=True):
    """Repository da monitorare per le build"""
    __tablename__ = "repositories"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    provider_id: int = Field(foreign_key="providers.id", index=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)
    type: int = Field(description="Repository type (GitLab, GitHub, etc)")
    name: str = Field(index=True, max_length=255)
    destination: str = Field(max_length=255)
    enabled: bool = Field(default=True, index=True)
    
    # Relationships
    provider: Provider = Relationship(back_populates="repositories")
    platform: Platform = Relationship(back_populates="repositories")
    builds: List["Build"] = Relationship(back_populates="repository")

class Build(SQLModel, table=True):
    """Build schedulata o eseguita (tabella partizionata per data)"""
    __tablename__ = "builds"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    repository_id: int = Field(foreign_key="repositories.id", index=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True) 
    tag: str = Field(max_length=255)
    date: datetime = Field(default_factory=datetime.utcnow, index=True)
    status: int = Field(default=BuildStatus.SCHEDULED, index=True)
    output: Optional[str] = Field(default=None)
    
    # Relationships
    repository: Repository = Relationship(back_populates="builds")
    platform: Platform = Relationship()
    artifacts: List["Artifact"] = Relationship(back_populates="build")
    installations: List["Installation"] = Relationship(back_populates="build")

class Artifact(SQLModel, table=True):
    """Artefatti prodotti da una build (tabella partizionata per build_id)"""
    __tablename__ = "artifacts"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    build_id: int = Field(foreign_key="builds.id", index=True)
    build_date: datetime = Field()
    hash: Optional[str] = Field(default=None, max_length=255, index=True)
    filename: str = Field(max_length=255, index=True)
    symlink_target: Optional[str] = Field(default=None, max_length=255)
    
    # Relationships
    build: Build = Relationship(back_populates="artifacts")

class Server(SQLModel, table=True):
    """Server di deployment"""
    __tablename__ = "servers"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)
    name: str = Field(max_length=255)
    prefix: str = Field(max_length=255)
    
    # Relationships
    platform: Platform = Relationship(back_populates="servers")
    hosts: List["Host"] = Relationship(back_populates="server")

class Facility(SQLModel, table=True):
    """Facility/Location dove sono installati gli host"""
    __tablename__ = "facilities"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=255)
    
    # Relationships
    hosts: List["Host"] = Relationship(back_populates="facility")

class Host(SQLModel, table=True):
    """Host fisici dove vengono installati i binari"""
    __tablename__ = "hosts"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    facility_id: int = Field(foreign_key="facilities.id", index=True)
    server_id: int = Field(foreign_key="servers.id", index=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)
    name: str = Field(unique=True, index=True, max_length=255)
    
    # Relationships
    facility: Facility = Relationship(back_populates="hosts")
    server: Server = Relationship(back_populates="hosts")
    platform: Platform = Relationship(back_populates="hosts")
    installations: List["Installation"] = Relationship(back_populates="host")

class User(SQLModel, table=True):
    """Utenti che possono installare"""
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=255)
    admin: bool = Field(default=False)
    notify: bool = Field(default=False)
    
    # Relationships
    installations: List["Installation"] = Relationship(back_populates="user")

class Installation(SQLModel, table=True):
    """Installazioni con supporto temporal (tabella partizionata per valid_from)"""
    __tablename__ = "installations"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    host_id: int = Field(foreign_key="hosts.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    build_id: int = Field(foreign_key="builds.id", index=True)
    build_date: datetime = Field()
    type: int = Field(description="Installation type")
    install_date: datetime = Field(index=True)
    valid_from: datetime = Field(default_factory=datetime.utcnow, index=True)
    valid_to: Optional[datetime] = Field(default=None, index=True)
    
    # Relationships
    host: Host = Relationship(back_populates="installations")
    user: User = Relationship(back_populates="installations")
    build: Build = Relationship(back_populates="installations")

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

def find_repository(session: Session, provider: Provider, project_path: str) -> Optional[Repository]:
    """Trova un repository abilitato per il progetto"""
    return session.exec(
        select(Repository).where(
            Repository.provider_id == provider.id,
            Repository.name == project_path,
            Repository.enabled == True
        )
    ).first()

# TODO Verificare con due piattaforme
# Endpoints
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
        
        # Estrai il tag dal ref
        tag = extract_tag_from_ref(webhook.ref)
        if not tag:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid tag reference"}
            )
        
        logger.info(f"Received tag push: {tag} for project {webhook.project.path_with_namespace}")
        
        # Estrai l'URL base dal git_http_url
        base_url = webhook.project.git_http_url.split("/")[0] + "//" + webhook.project.git_http_url.split("/")[2]
        provider = session.exec(select(Provider).where(Provider.url == base_url)).first()
        
        # Trova il repository
        repository = find_repository(session, provider, webhook.project.path_with_namespace)
        
        if not repository:
            logger.warning(f"Repository {webhook.project.path_with_namespace} not found or not enabled")
            return JSONResponse(
                status_code=200,
                content={"message": f"Repository {webhook.project.path_with_namespace} not configured for builds"}
            )
        
        """Schedula le build per tutte le piattaforme abilitate del repository"""
        platforms = session.exec(
            select(Platform).join(Repository).where(
                Repository.provider_id == repository.provider_id,
                Repository.name == repository.name,
                Repository.enabled == True
            )
        ).all()
        
        builds = []
        for platform in platforms:
            # Verifica se esiste già una build per questo tag e piattaforma
            existing_build = session.exec(
                select(Build).where(
                    Build.repository_id == repository.id,
                    Build.platform_id == platform.id,
                    Build.tag == tag
                )
            ).first()
            
            if not existing_build:
                build = Build(
                    repository_id=repository.id,
                    platform_id=platform.id,
                    tag=tag,
                    status=BuildStatus.SCHEDULED
                )
                session.add(build)
                builds.append(build)
        
        session.commit()

        for build in builds:
            """Notifica il worker Celery di una nuova build da processare"""
            # TODO: Implementare la notifica a Celery
            logger.info(f"Build {build.id} scheduled for processing")

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

#@app.post("/webhook/github")
#async def handle_github_webhook(
#    payload: dict,
#    session: Session = Depends(get_session)
#):
#    """
#    Placeholder per gestire webhook di GitHub
#    """
#    # TODO: Implementare la gestione dei webhook GitHub
#    return JSONResponse(
#        status_code=501,
#        content={"error": "GitHub webhooks not yet implemented"}
#    )
