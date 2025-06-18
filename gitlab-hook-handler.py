from fastapi import FastAPI, HTTPException, Request, Depends, BackgroundTasks
from sqlmodel import Field, SQLModel, Session, create_engine, select, Relationship
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database models based on schema.sql
class Architecture(SQLModel, table=True):
    __tablename__ = "architectures"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, unique=True)
    
    # Relationships
    platforms: List["Platform"] = Relationship(back_populates="architecture")

class Distribution(SQLModel, table=True):
    __tablename__ = "distributions"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    version: str = Field(max_length=255)
    
    # Relationships
    platforms: List["Platform"] = Relationship(back_populates="distribution")

class Platform(SQLModel, table=True):
    __tablename__ = "platforms"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    distribution_id: int = Field(foreign_key="distributions.id", index=True)
    architecture_id: int = Field(foreign_key="architectures.id", index=True)
    
    # Relationships
    distribution: Distribution = Relationship(back_populates="platforms")
    architecture: Architecture = Relationship(back_populates="platforms")
    repositories: List["Repository"] = Relationship(back_populates="platform")
    builds: List["Build"] = Relationship(back_populates="platform")
    servers: List["Server"] = Relationship(back_populates="platform")
    hosts: List["Host"] = Relationship(back_populates="platform")

class Provider(SQLModel, table=True):
    __tablename__ = "providers"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(max_length=255, unique=True)
    
    # Relationships
    repositories: List["Repository"] = Relationship(back_populates="provider")

class Repository(SQLModel, table=True):
    __tablename__ = "repositories"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    provider_id: int = Field(foreign_key="providers.id", index=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)
    type: int
    name: str = Field(max_length=255, index=True)
    destination: str = Field(max_length=255)
    enabled: bool = Field(default=True)
    
    # Relationships
    provider: Provider = Relationship(back_populates="repositories")
    platform: Platform = Relationship(back_populates="repositories")
    builds: List["Build"] = Relationship(back_populates="repository")

class Build(SQLModel, table=True):
    __tablename__ = "builds"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    repository_id: int = Field(foreign_key="repositories.id", index=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)  # NOT NULL
    tag: str = Field(max_length=255)
    date: datetime = Field(index=True)
    status: Optional[int] = None
    output: Optional[str] = None
    
    # Relationships
    repository: Repository = Relationship(back_populates="builds")
    platform: Platform = Relationship(back_populates="builds")  # Required relationship
    artifacts: List["Artifact"] = Relationship(back_populates="build")
    installations: List["Installation"] = Relationship(back_populates="build")

class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    build_id: int = Field(foreign_key="builds.id", index=True)
    build_date: datetime
    hash: Optional[str] = Field(max_length=255, nullable=True, index=True)
    filename: str = Field(max_length=255, index=True)
    symlink_target: Optional[str] = Field(max_length=255, nullable=True)
    
    # Relationships
    build: Build = Relationship(back_populates="artifacts")

class Server(SQLModel, table=True):
    __tablename__ = "servers"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)
    name: str = Field(max_length=255)
    prefix: str = Field(max_length=255)
    
    # Relationships
    platform: Platform = Relationship(back_populates="servers")
    hosts: List["Host"] = Relationship(back_populates="server")

class Facility(SQLModel, table=True):
    __tablename__ = "facilities"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, unique=True)
    
    # Relationships
    hosts: List["Host"] = Relationship(back_populates="facility")

class Host(SQLModel, table=True):
    __tablename__ = "hosts"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    facility_id: int = Field(foreign_key="facilities.id", index=True)
    server_id: int = Field(foreign_key="servers.id", index=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)  # NOT NULL
    name: str = Field(max_length=255, unique=True)
    
    # Relationships
    facility: Facility = Relationship(back_populates="hosts")
    server: Server = Relationship(back_populates="hosts")
    platform: Platform = Relationship(back_populates="hosts")  # Required relationship
    installations: List["Installation"] = Relationship(back_populates="host")

class User(SQLModel, table=True):
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, unique=True)
    admin: bool = Field(default=False)
    notify: bool = Field(default=False)
    
    # Relationships
    installations: List["Installation"] = Relationship(back_populates="user")

class Installation(SQLModel, table=True):
    __tablename__ = "installations"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    host_id: int = Field(foreign_key="hosts.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    build_id: int = Field(index=True)
    build_date: datetime
    type: int
    install_date: datetime = Field(index=True)
    valid_from: datetime = Field(index=True)
    valid_to: Optional[datetime] = Field(nullable=True)
    
    # Relationships
    host: Host = Relationship(back_populates="installations")
    user: User = Relationship(back_populates="installations")
    build: Build = Relationship(back_populates="installations")

# Pydantic models for request/response
class GitLabProject(BaseModel):
    path_with_namespace: str
    ssh_url: str

class GitLabWebhookPayload(BaseModel):
    ref: str
    user_username: str
    user_email: str
    project: GitLabProject

class ExtractedData(BaseModel):
    ref: str
    user_username: str
    user_email: str
    path_with_namespace: str
    ssh_url: str
    email: str
    tag: Optional[str] = None

class WebhookResponse(BaseModel):
    status: str
    message: str
    repository: Optional[Dict[str, Any]] = None
    extracted_data: ExtractedData
    build_scheduled: bool = False

# Database configuration
DATABASE_URL = "postgresql://inau:Inau123@localhost:5432/inau"
engine = create_engine(DATABASE_URL, echo=True)

def get_session():
    with Session(engine) as session:
        yield session

# Function to add build to database
def create_build(
    session: Session,
    repository_id: int,
    platform_id: int,
    tag: str
):
    """Create a new build entry in the database"""
    build = Build(
        repository_id=repository_id,
        platform_id=platform_id,
        tag=tag,
        date=datetime.now(),
        status=0  # Assumo 0 = scheduled/pending
    )
    session.add(build)
    session.commit()
    session.refresh(build)
    logger.info(f"Build created: {build.id} for repository {repository_id}, tag {tag}")
    return build

# Function to schedule build task with Celery (placeholder)
def schedule_build_task(repository_id: int, platform_id: int, tag: str):
    """Schedule a build task using Celery (this is a placeholder)"""
    # This would normally call a Celery task
    # e.g.: build_task.delay(repository_id, platform_id, tag)
    logger.info(f"Build task scheduled for repository {repository_id}, platform {platform_id}, tag {tag}")
    return True

# Lifespan event to create tables
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tables are already created in the schema.sql, so we don't need to create them here
    # Only create them if they don't exist in development
    # SQLModel.metadata.create_all(engine)
    yield

# Create FastAPI app
app = FastAPI(lifespan=lifespan)

@app.post("", response_model=WebhookResponse)
async def gitlab_webhook(
    webhook_data: GitLabWebhookPayload,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session)
):
    """Handle GitLab webhook POST requests"""
    
    try:
        # Extract tag from ref (format: refs/tags/1.0.0)
        tag = webhook_data.ref.replace("refs/tags/", "") if webhook_data.ref.startswith("refs/tags/") else None
        
        # Extract required fields
        extracted_data = ExtractedData(
            ref=webhook_data.ref,
            user_username=webhook_data.user_username,
            user_email=webhook_data.user_email,
            path_with_namespace=webhook_data.project.path_with_namespace,
            ssh_url=webhook_data.project.ssh_url,
            email=webhook_data.user_email,
            tag=tag
        )
        
        # Log extracted data for debugging
        logger.info(f"Extracted webhook data: {extracted_data.model_dump()}")
        
        # Only process tag pushes
        if not tag:
            return WebhookResponse(
                status="ignored",
                message="Not a tag push event",
                extracted_data=extracted_data,
                build_scheduled=False
            )
        
        # Search for repository in database
        statement = select(Repository).where(
            Repository.name == extracted_data.path_with_namespace,
            Repository.enabled == True
        )
        repository = session.exec(statement).first()
        
        if repository:
            logger.info(f"Repository found: {repository.id} - {repository.name}")
            
            # Since platform_id is now NOT NULL in repository, we can directly use it
            # Create build entry in database
            build = create_build(
                session, 
                repository.id, 
                repository.platform_id,  # Always present now
                tag
            )
            
            # Schedule the actual build task (this would use Celery in a real implementation)
            background_tasks.add_task(
                schedule_build_task,
                repository.id,
                repository.platform_id,
                tag
            )
            
            return WebhookResponse(
                status="success",
                message=f"Repository found and build scheduled for tag {tag}",
                repository=repository.model_dump(),
                extracted_data=extracted_data,
                build_scheduled=True
            )
        else:
            logger.info(f"Repository not found: {extracted_data.path_with_namespace}")
            return WebhookResponse(
                status="not_found",
                message="Repository not found",
                repository=None,
                extracted_data=extracted_data,
                build_scheduled=False
            )
            
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
