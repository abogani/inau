"""
INAU REST API
Fornisce API RESTful per consultare builds, artifacts e installare binari
"""
import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pathlib import Path
import base64
from contextlib import asynccontextmanager
from enum import IntEnum

from fastapi import FastAPI, HTTPException, Depends, Header, Query, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlmodel import Session, create_engine, select, func, and_, or_, SQLModel, selectinload
from pydantic import BaseModel, Field, validator
import paramiko
import ldap
from smtplib import SMTP
from email.mime.text import MIMEText

# Import dei modelli dal models.py
from models import (
    Architecture, Distribution, Platform, Provider, Repository,
    Build, Artifact, Builder, Server, Facility, Host, User, Installation,
    RepositoryType, BuildStatus, InstallationType, AuthenticationType
)

# Configurazione
DATABASE_URL = os.getenv('DATABASE_URL', None)
LDAP_URL = os.getenv('LDAP_URL', None)
SMTP_SERVER = os.getenv('SMTP_SERVER', None)
SMTP_DOMAIN = os.getenv('SMTP_DOMAIN', None)
SMTP_SENDER = os.getenv('SMTP_SENDER', None)
REPO_DIR = os.getenv('INAU_REPO_DIR', None)
STORE_DIR = os.getenv('INAU_STORE_DIR', None)

# Setup database
engine = create_engine(DATABASE_URL, echo=False)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Modelli Pydantic per le richieste/risposte

class UserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

class UserResponse(BaseModel):
    id: int
    name: str
    admin: bool
    notify: bool

class ArchitectureRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

class DistributionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    version: str = Field(..., min_length=1, max_length=255)

class PlatformRequest(BaseModel):
    distribution: str
    version: str
    architecture: str

class PlatformResponse(BaseModel):
    id: int
    distribution: str
    version: str
    architecture: str

class BuilderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    distribution: str
    version: str
    architecture: str
    environment: Optional[str] = None

class BuilderResponse(BaseModel):
    name: str
    distribution: str
    version: str
    architecture: str
    environment: Optional[str]

class ServerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    prefix: str = Field(..., min_length=1, max_length=255)
    distribution: str
    version: str
    architecture: str

class ServerResponse(BaseModel):
    name: str
    prefix: str
    distribution: str
    version: str
    architecture: str

class ProviderRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=255)

class RepositoryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: str
    distribution: str
    version: str
    architecture: str
    type: str = Field(..., regex="^(cplusplus|python|shellscript|configuration|library)$")
    destination: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True

class RepositoryResponse(BaseModel):
    id: int
    name: str
    provider: str
    distribution: str
    version: str
    architecture: str
    type: str
    destination: str
    enabled: bool

class FacilityRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

class HostRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    server: str
    prefix: str

class HostResponse(BaseModel):
    name: str
    server: str
    facility: str

class InstallationRequest(BaseModel):
    repository: str = Field(..., min_length=1)
    tag: str = Field(..., min_length=1)

class InstallationResponse(BaseModel):
    facility: str
    host: str
    repository: str
    tag: str
    date: datetime
    author: str

class BuildResponse(BaseModel):
    id: int
    repository: str
    platform: str
    tag: str
    date: datetime
    status: int
    status_name: str

class ArtifactResponse(BaseModel):
    id: int
    filename: str
    hash: Optional[str]
    symlink_target: Optional[str]

# FastAPI app con lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestione del ciclo di vita dell'applicazione"""
    logger.info("Starting INAU REST API...")
    SQLModel.metadata.create_all(engine)
    yield
    logger.info("Shutting down INAU REST API...")

app = FastAPI(
    title="INAU REST API",
    version="2.0.0",
    lifespan=lifespan
)

# Dependency per la sessione database
def get_session():
    with Session(engine) as session:
        yield session

# Dependency per l'autenticazione
async def authenticate(
    auth_type: AuthenticationType = AuthenticationType.USER,
    authorization: Optional[str] = Header(None),
    session: Session = Depends(get_session)
) -> str:
    """Autentica l'utente tramite LDAP"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    try:
        # Decodifica credenziali Basic Auth
        scheme, credentials = authorization.split()
        if scheme.lower() != 'basic':
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
        
        decoded = base64.b64decode(credentials).decode('utf-8')
        username, password = decoded.split(':', 1)
        
        # Verifica che l'utente esista nel database
        user = session.exec(select(User).where(User.name == username)).first()
        if not user:
            raise HTTPException(status_code=403, detail="User not enabled")
        
        # Se richiesto admin, verifica i permessi
        if auth_type == AuthenticationType.ADMIN and not user.admin:
            raise HTTPException(status_code=403, detail="Admin privileges required")
        
        # Autenticazione LDAP
        try:
            auth = ldap.initialize(LDAP_URL, bytes_mode=False)
            auth.simple_bind_s(f"uid={username},ou=people,dc=elettra,dc=eu", password)
            auth.unbind_s()
        except Exception as e:
            logger.error(f"LDAP authentication failed: {str(e)}")
            raise HTTPException(status_code=403, detail="Authentication failed")
        
        return username
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

# Funzioni di utilità

def send_email(recipients: List[str], subject: str, body: str):
    """Invia email di notifica"""
    try:
        if not recipients:
            return
            
        msg = MIMEText(body)
        msg['Subject'] = f"INAU. {subject}"
        msg['From'] = f"{SMTP_SENDER}@{SMTP_DOMAIN}"
        msg['To'] = ', '.join(recipients)
        
        with SMTP(SMTP_SERVER, 25) as smtp:
            smtp.send_message(msg)
            
    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}")

def send_email_admins(subject: str, body: str, session: Session):
    """Invia email agli amministratori"""
    admins = session.exec(select(User).where(User.admin == True)).all()
    recipients = [f"{admin.name}@{SMTP_DOMAIN}" for admin in admins]
    send_email(recipients, subject, body)

def format_plain_text_response(data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> str:
    """Formatta la risposta in plain text con colonne allineate"""
    if isinstance(data, dict):
        # Se è un dizionario con 'message', mostralo
        if 'message' in data:
            return f"message: {data['message']}"
        data = [data]
    
    if not data:
        return ""
    
    # Calcola larghezza massima per ogni colonna
    col_widths = {}
    for item in data:
        for key, value in item.items():
            max_width = max(len(str(key)), len(str(value)))
            col_widths[key] = max(col_widths.get(key, 0), max_width)
    
    # Costruisci l'output
    lines = []
    
    # Header
    header = "  ".join(key.ljust(col_widths[key]) for key in data[0].keys())
    lines.append(header)
    
    # Separator
    separator = "--".join("-" * col_widths[key] for key in data[0].keys())
    lines.append(separator)
    
    # Rows
    for item in data:
        row = "  ".join(str(item[key]).ljust(col_widths[key]) for key in item.keys())
        lines.append(row)
    
    return "\n".join(lines)

class AcceptMiddleware:
    """Middleware per gestire Accept header e formato risposta"""
    def __init__(self, app):
        self.app = app
        
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            accept = headers.get(b"accept", b"application/json").decode()
            scope["accept"] = accept
        await self.app(scope, receive, send)

app.add_middleware(AcceptMiddleware)

# Funzione helper per determinare il formato di risposta
def get_response_format(accept: str = Header("application/json")) -> str:
    """Determina il formato di risposta basato sull'Accept header"""
    if "text/plain" in accept:
        return "text/plain"
    return "application/json"

# Endpoints root

@app.get("/v2/cs")
async def get_cs_info(accept: str = Header("application/json")):
    """Lista i subpath disponibili"""
    data = [
        {'subpath': 'users'},
        {'subpath': 'distributions'},
        {'subpath': 'architectures'},
        {'subpath': 'platforms'},
        {'subpath': 'builders'},
        {'subpath': 'servers'},
        {'subpath': 'providers'},
        {'subpath': 'repositories'},
        {'subpath': 'facilities'},
        {'subpath': 'builds'},
        {'subpath': 'installations'}
    ]
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    return data

# Endpoints Users

@app.get("/v2/cs/users", response_model=List[UserResponse])
async def get_users(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutti gli utenti"""
    users = session.exec(select(User)).all()
    
    if "text/plain" in accept:
        data = [{"name": u.name} for u in users]
        return PlainTextResponse(format_plain_text_response(data))
    
    return users

@app.post("/v2/cs/users", response_model=UserResponse, status_code=201)
async def create_user(
    user: UserRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea un nuovo utente (richiede admin)"""
    db_user = User(name=user.name, admin=False, notify=False)
    session.add(db_user)
    
    try:
        session.commit()
        session.refresh(db_user)
        return db_user
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=422, detail="User already exists")

@app.put("/v2/cs/users/{username}", response_model=UserResponse)
async def update_user(
    username: str,
    user: UserRequest,
    auth_user: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Aggiorna un utente (richiede admin)"""
    db_user = session.exec(select(User).where(User.name == username)).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    db_user.name = user.name
    session.commit()
    session.refresh(db_user)
    return db_user

@app.delete("/v2/cs/users/{username}", status_code=204)
async def delete_user(
    username: str,
    auth_user: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Elimina un utente (richiede admin)"""
    db_user = session.exec(select(User).where(User.name == username)).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    session.delete(db_user)
    session.commit()

# Endpoints Architectures

@app.get("/v2/cs/architectures")
async def get_architectures(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutte le architetture"""
    architectures = session.exec(select(Architecture)).all()
    
    if "text/plain" in accept:
        data = [{"name": a.name} for a in architectures]
        return PlainTextResponse(format_plain_text_response(data))
    
    return [{"name": a.name} for a in architectures]

@app.post("/v2/cs/architectures", status_code=201)
async def create_architecture(
    arch: ArchitectureRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea una nuova architettura (richiede admin)"""
    db_arch = Architecture(name=arch.name)
    session.add(db_arch)
    
    try:
        session.commit()
        session.refresh(db_arch)
        return {"name": db_arch.name}
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Architecture already exists")

# Endpoints Distributions

@app.get("/v2/cs/distributions")
async def get_distributions(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutte le distribuzioni"""
    distributions = session.exec(select(Distribution)).all()
    
    data = [{"id": d.id, "name": d.name, "version": d.version} for d in distributions]
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/distributions", status_code=201)
async def create_distribution(
    dist: DistributionRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea una nuova distribuzione (richiede admin)"""
    db_dist = Distribution(name=dist.name, version=dist.version)
    session.add(db_dist)
    
    try:
        session.commit()
        session.refresh(db_dist)
        return {"id": db_dist.id, "name": db_dist.name, "version": db_dist.version}
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Distribution already exists")

# Endpoints Platforms

@app.get("/v2/cs/platforms", response_model=List[PlatformResponse])
async def get_platforms(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutte le piattaforme"""
    platforms = session.exec(
        select(Platform)
        .options(
            selectinload(Platform.distribution),
            selectinload(Platform.architecture)
        )
    ).all()
    
    data = []
    for p in platforms:
        data.append({
            "id": p.id,
            "distribution": p.distribution.name,
            "version": p.distribution.version,
            "architecture": p.architecture.name
        })
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/platforms", response_model=PlatformResponse, status_code=201)
async def create_platform(
    platform: PlatformRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea una nuova piattaforma (richiede admin)"""
    # Trova distribuzione e architettura
    dist = session.exec(
        select(Distribution).where(
            Distribution.name == platform.distribution,
            Distribution.version == platform.version
        )
    ).first()
    if not dist:
        raise HTTPException(status_code=404, detail="Distribution not found")
    
    arch = session.exec(
        select(Architecture).where(Architecture.name == platform.architecture)
    ).first()
    if not arch:
        raise HTTPException(status_code=404, detail="Architecture not found")
    
    db_platform = Platform(distribution_id=dist.id, architecture_id=arch.id)
    session.add(db_platform)
    
    try:
        session.commit()
        session.refresh(db_platform)
        return {
            "id": db_platform.id,
            "distribution": dist.name,
            "version": dist.version,
            "architecture": arch.name
        }
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Platform already exists")

# Endpoints Builders

@app.get("/v2/cs/builders", response_model=List[BuilderResponse])
async def get_builders(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutti i builders"""
    builders = session.exec(
        select(Builder)
        .options(
            selectinload(Builder.platform)
            .selectinload(Platform.distribution),
            selectinload(Builder.platform)
            .selectinload(Platform.architecture)
        )
    ).all()
    
    data = []
    for b in builders:
        data.append({
            "name": b.name,
            "distribution": b.platform.distribution.name,
            "version": b.platform.distribution.version,
            "architecture": b.platform.architecture.name,
            "environment": b.environment
        })
    
    if "text/plain" in accept:
        # Per text/plain, rimuovi i campi None
        text_data = []
        for d in data:
            if d["environment"] is None:
                d = {k: v for k, v in d.items() if k != "environment"}
            text_data.append(d)
        return PlainTextResponse(format_plain_text_response(text_data))
    
    return data

@app.post("/v2/cs/builders", response_model=BuilderResponse, status_code=201)
async def create_builder(
    builder: BuilderRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea un nuovo builder (richiede admin)"""
    # Trova la piattaforma
    dist = session.exec(
        select(Distribution).where(
            Distribution.name == builder.distribution,
            Distribution.version == builder.version
        )
    ).first()
    if not dist:
        raise HTTPException(status_code=404, detail="Distribution not found")
    
    arch = session.exec(
        select(Architecture).where(Architecture.name == builder.architecture)
    ).first()
    if not arch:
        raise HTTPException(status_code=404, detail="Architecture not found")
    
    platform = session.exec(
        select(Platform).where(
            Platform.distribution_id == dist.id,
            Platform.architecture_id == arch.id
        )
    ).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")
    
    db_builder = Builder(
        name=builder.name,
        platform_id=platform.id,
        environment=builder.environment
    )
    session.add(db_builder)
    
    try:
        session.commit()
        session.refresh(db_builder)
        return {
            "name": db_builder.name,
            "distribution": dist.name,
            "version": dist.version,
            "architecture": arch.name,
            "environment": db_builder.environment
        }
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Builder already exists")

# Endpoints Facilities

@app.get("/v2/cs/facilities")
async def get_facilities(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutte le facilities"""
    facilities = session.exec(select(Facility)).all()
    
    data = [{"name": f.name} for f in facilities]
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/facilities", status_code=201)
async def create_facility(
    facility: FacilityRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea una nuova facility (richiede admin)"""
    db_facility = Facility(name=facility.name)
    session.add(db_facility)
    
    try:
        session.commit()
        session.refresh(db_facility)
        return {"name": db_facility.name}
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Facility already exists")

# Endpoints Hosts

@app.get("/v2/cs/facilities/{facility_name}/hosts", response_model=List[HostResponse])
async def get_hosts(
    facility_name: str,
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutti gli hosts di una facility"""
    facility = session.exec(
        select(Facility).where(Facility.name == facility_name)
    ).first()
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    
    hosts = session.exec(
        select(Host)
        .where(Host.facility_id == facility.id)
        .options(selectinload(Host.server))
    ).all()
    
    data = []
    for h in hosts:
        data.append({
            "name": h.name,
            "server": h.server.name,
            "facility": facility.name
        })
    
    if "text/plain" in accept:
        # Per text/plain, mostra solo i nomi
        text_data = [{"name": h.name} for h in hosts]
        return PlainTextResponse(format_plain_text_response(text_data))
    
    return data

@app.post("/v2/cs/facilities/{facility_name}/hosts", response_model=HostResponse, status_code=201)
async def create_host(
    facility_name: str,
    host: HostRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea un nuovo host (richiede admin)"""
    facility = session.exec(
        select(Facility).where(Facility.name == facility_name)
    ).first()
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    
    server = session.exec(
        select(Server).where(
            Server.name == host.server,
            Server.prefix == host.prefix
        )
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    db_host = Host(
        name=host.name,
        facility_id=facility.id,
        server_id=server.id,
        platform_id=server.platform_id
    )
    session.add(db_host)
    
    try:
        session.commit()
        session.refresh(db_host)
        return {
            "name": db_host.name,
            "server": server.name,
            "facility": facility.name
        }
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Host already exists")

# Endpoints Builds

@app.get("/v2/cs/builds")
async def get_builds(
    repository: Optional[str] = Query(None),
    platform_id: Optional[int] = Query(None),
    tag: Optional[str] = Query(None),
    status: Optional[int] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista le builds con filtri opzionali"""
    query = select(Build).options(
        selectinload(Build.repository),
        selectinload(Build.platform)
        .selectinload(Platform.distribution),
        selectinload(Build.platform)
        .selectinload(Platform.architecture)
    )
    
    # Applica filtri
    if repository:
        query = query.join(Repository).where(Repository.name == repository)
    if platform_id:
        query = query.where(Build.platform_id == platform_id)
    if tag:
        query = query.where(Build.tag == tag)
    if status is not None:
        query = query.where(Build.status == status)
    
    # Ordina per data decrescente
    query = query.order_by(Build.date.desc()).limit(limit).offset(offset)
    
    builds = session.exec(query).all()
    
    data = []
    for b in builds:
        platform_str = f"{b.platform.distribution.name} {b.platform.distribution.version} {b.platform.architecture.name}"
        status_names = {
            0: "SCHEDULED",
            1: "RUNNING", 
            2: "SUCCESS",
            3: "FAILED",
            4: "CANCELLED"
        }
        data.append({
            "id": b.id,
            "repository": b.repository.name,
            "platform": platform_str,
            "tag": b.tag,
            "date": b.date,
            "status": b.status,
            "status_name": status_names.get(b.status, "UNKNOWN")
        })
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.get("/v2/cs/builds/{build_id}")
async def get_build(
    build_id: int,
    session: Session = Depends(get_session)
):
    """Ottiene i dettagli di una build specifica"""
    build = session.exec(
        select(Build)
        .where(Build.id == build_id)
        .options(
            selectinload(Build.repository),
            selectinload(Build.platform)
        )
    ).first()
    
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    
    return {
        "id": build.id,
        "repository": build.repository.name,
        "platform_id": build.platform_id,
        "tag": build.tag,
        "date": build.date,
        "status": build.status,
        "output": build.output
    }

@app.get("/v2/cs/builds/{build_id}/artifacts", response_model=List[ArtifactResponse])
async def get_build_artifacts(
    build_id: int,
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista gli artifacts di una build"""
    build = session.get(Build, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    
    artifacts = session.exec(
        select(Artifact).where(Artifact.build_id == build_id)
    ).all()
    
    data = []
    for a in artifacts:
        data.append({
            "id": a.id,
            "filename": a.filename,
            "hash": a.hash,
            "symlink_target": a.symlink_target
        })
    
    if "text/plain" in accept:
        # Per text/plain, mostra solo i filename
        text_data = [{"filename": a.filename} for a in artifacts]
        return PlainTextResponse(format_plain_text_response(text_data))
    
    return data

# Endpoints Repositories

@app.get("/v2/cs/repositories", response_model=List[RepositoryResponse])
async def get_repositories(
    enabled: Optional[bool] = Query(None),
    platform_id: Optional[int] = Query(None),
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutti i repository con filtri opzionali"""
    query = select(Repository).options(
        selectinload(Repository.provider),
        selectinload(Repository.platform)
        .selectinload(Platform.distribution),
        selectinload(Repository.platform)
        .selectinload(Platform.architecture)
    )
    
    if enabled is not None:
        query = query.where(Repository.enabled == enabled)
    if platform_id:
        query = query.where(Repository.platform_id == platform_id)
    
    repositories = session.exec(query).all()
    
    data = []
    for r in repositories:
        type_names = {
            0: "cplusplus",
            1: "python",
            2: "configuration",
            3: "shellscript",
            4: "library"
        }
        data.append({
            "id": r.id,
            "name": r.name,
            "provider": r.provider.url,
            "distribution": r.platform.distribution.name,
            "version": r.platform.distribution.version,
            "architecture": r.platform.architecture.name,
            "type": type_names.get(r.type, "unknown"),
            "destination": r.destination,
            "enabled": r.enabled
        })
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/repositories", response_model=RepositoryResponse, status_code=201)
async def create_repository(
    repo: RepositoryRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea un nuovo repository (richiede admin)"""
    # Trova provider e piattaforma
    provider = session.exec(
        select(Provider).where(Provider.url == repo.provider)
    ).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    
    dist = session.exec(
        select(Distribution).where(
            Distribution.name == repo.distribution,
            Distribution.version == repo.version
        )
    ).first()
    if not dist:
        raise HTTPException(status_code=404, detail="Distribution not found")
    
    arch = session.exec(
        select(Architecture).where(Architecture.name == repo.architecture)
    ).first()
    if not arch:
        raise HTTPException(status_code=404, detail="Architecture not found")
    
    platform = session.exec(
        select(Platform).where(
            Platform.distribution_id == dist.id,
            Platform.architecture_id == arch.id
        )
    ).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")
    
    # Mappa tipo
    type_map = {
        "cplusplus": 0,
        "python": 1,
        "configuration": 2,
        "shellscript": 3,
        "library": 4
    }
    
    db_repo = Repository(
        name=repo.name,
        provider_id=provider.id,
        platform_id=platform.id,
        type=type_map[repo.type],
        destination=repo.destination,
        enabled=repo.enabled
    )
    session.add(db_repo)
    
    try:
        session.commit()
        session.refresh(db_repo)
        return {
            "id": db_repo.id,
            "name": db_repo.name,
            "provider": provider.url,
            "distribution": dist.name,
            "version": dist.version,
            "architecture": arch.name,
            "type": repo.type,
            "destination": db_repo.destination,
            "enabled": db_repo.enabled
        }
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Repository already exists")

# Endpoints Installations

def install(
    username: str,
    reponame: str,
    tag: str,
    destinations: Dict[Server, List[Host]],
    itype: InstallationType,
    session: Session
) -> List[Dict[str, Any]]:
    """Logica di installazione comune"""
    now = datetime.utcnow()
    retval = []
    
    user = session.exec(select(User).where(User.name == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not destinations:
        raise HTTPException(status_code=404, detail="No destinations found")
    
    for server, hosts in destinations.items():
        # Trova il repository per questa piattaforma
        repository = session.exec(
            select(Repository).where(
                Repository.platform_id == server.platform_id,
                Repository.name == reponame
            )
        ).first()
        if not repository:
            continue
        
        # Trova la build
        build = session.exec(
            select(Build).where(
                Build.repository_id == repository.id,
                Build.tag == tag,
                Build.status == BuildStatus.SUCCESS
            ).order_by(Build.id.desc())
        ).first()
        
        if not build:
            raise HTTPException(
                status_code=404,
                detail=f"Build not available for {reponame} tag {tag}. Check annotated tag."
            )
        
        try:
            # Connessione SSH al server
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(
                    hostname=server.name,
                    port=22,
                    username="root",
                    key_filename=os.path.expanduser("~/.ssh/id_rsa")
                )
                
                with ssh.open_sftp() as sftp:
                    # Installa gli artifacts
                    artifacts = session.exec(
                        select(Artifact).where(Artifact.build_id == build.id)
                    ).all()
                    
                    for artifact in artifacts:
                        if artifact.hash:
                            # File normale
                            hash_path = Path(STORE_DIR) / artifact.hash[:2] / artifact.hash[2:4] / artifact.hash
                            temp_path = f"/tmp/{artifact.hash}"
                            
                            # Copia il file
                            sftp.put(str(hash_path), temp_path)
                            
                            # Determina permessi
                            filemode = "755"
                            if repository.type == RepositoryType.CONFIGURATION:
                                filemode = "644"
                            
                            # Installa il file
                            if itype == InstallationType.GLOBAL or itype == InstallationType.FACILITY:
                                dest_path = f"{server.prefix}{repository.destination}{artifact.filename}"
                            else:  # HOST
                                dest_path = f"{server.prefix}/site/{hosts[0].name}/{repository.destination}{artifact.filename}"
                            
                            # Crea directory e installa
                            ssh.exec_command(f"mkdir -p $(dirname {dest_path})")
                            ssh.exec_command(f"install -m{filemode} {temp_path} {dest_path}")
                            ssh.exec_command(f"rm {temp_path}")
                        else:
                            # Symlink
                            if itype == InstallationType.GLOBAL or itype == InstallationType.FACILITY:
                                link_path = f"{server.prefix}{artifact.filename}"
                                target_path = f"{server.prefix}{artifact.symlink_target}"
                            else:  # HOST
                                link_path = f"{server.prefix}/site/{hosts[0].name}/{artifact.filename}"
                                target_path = f"{server.prefix}/site/{hosts[0].name}/{artifact.symlink_target}"
                            
                            ssh.exec_command(f"ln -sfn {target_path} {link_path}")
        
        except Exception as e:
            logger.error(f"Installation error: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
        
        # Registra le installazioni
        for host in hosts:
            installation = Installation(
                user_id=user.id,
                host_id=host.id,
                build_id=build.id,
                build_date=build.date,
                type=int(itype),
                install_date=now,
                valid_from=now
            )
            session.add(installation)
            
            retval.append({
                'facility': host.facility.name,
                'host': host.name,
                'repository': repository.name,
                'tag': build.tag,
                'date': installation.install_date,
                'author': user.name
            })
    
    session.commit()
    
    # Invia notifiche
    subject = f"Installation: {reponame} {tag}"
    body = f"Installed {reponame} tag {tag} on {len(retval)} hosts"
    
    recipients = set()
    if user.notify:
        recipients.add(f"{user.name}@{SMTP_DOMAIN}")
    
    send_email(list(recipients), subject, body)
    
    return retval

@app.get("/v2/cs/installations", response_model=List[InstallationResponse])
async def get_installations(
    mode: str = Query("status", regex="^(status|diff|history)$"),
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista le installazioni globali"""
    # Subquery per le ultime installazioni
    latest_subq = (
        select(
            Installation.id,
            func.row_number().over(
                partition_by=(Installation.host_id, Build.repository_id),
                order_by=Installation.id.desc()
            ).label('rn')
        )
        .join(Build, Installation.build_id == Build.id)
        .where(Installation.valid_to == None)
        .subquery()
    )
    
    query = select(Installation).options(
        selectinload(Installation.user),
        selectinload(Installation.host)
        .selectinload(Host.facility),
        selectinload(Installation.build)
        .selectinload(Build.repository)
    )
    
    if mode == "status":
        query = query.join(
            latest_subq,
            and_(Installation.id == latest_subq.c.id, latest_subq.c.rn == 1)
        )
    elif mode == "diff":
        query = query.join(
            latest_subq,
            and_(Installation.id == latest_subq.c.id, latest_subq.c.rn == 1)
        ).where(Installation.type != InstallationType.GLOBAL)
    
    query = query.order_by(Installation.install_date.desc())
    installations = session.exec(query).all()
    
    data = []
    for i in installations:
        data.append({
            "facility": i.host.facility.name,
            "host": i.host.name,
            "repository": i.build.repository.name,
            "tag": i.build.tag,
            "date": i.install_date,
            "author": i.user.name
        })
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/installations", response_model=List[InstallationResponse])
async def create_global_installation(
    req: InstallationRequest,
    username: str = Depends(authenticate),
    session: Session = Depends(get_session)
):
    """Installa globalmente su tutti gli host"""
    destinations = {}
    
    # Trova tutti i repository abilitati con questo nome
    repositories = session.exec(
        select(Repository)
        .where(Repository.name == req.repository, Repository.enabled == True)
    ).all()
    
    if not repositories:
        raise HTTPException(status_code=404, detail="Repository not found or not enabled")
    
    # Per ogni repository, trova tutti gli host
    for repo in repositories:
        servers = session.exec(
            select(Server).where(Server.platform_id == repo.platform_id)
        ).all()
        
        for server in servers:
            hosts = session.exec(
                select(Host)
                .where(Host.server_id == server.id)
                .options(selectinload(Host.facility))
            ).all()
            if hosts:
                destinations[server] = hosts
    
    return install(username, req.repository, req.tag, destinations, InstallationType.GLOBAL, session)

@app.get("/v2/cs/facilities/{facility_name}/installations")
async def get_facility_installations(
    facility_name: str,
    mode: str = Query("status", regex="^(status|diff|history)$"),
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista le installazioni di una facility"""
    facility = session.exec(
        select(Facility).where(Facility.name == facility_name)
    ).first()
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    
    # Subquery per le ultime installazioni
    latest_subq = (
        select(
            Installation.id,
            func.row_number().over(
                partition_by=(Installation.host_id, Build.repository_id),
                order_by=Installation.id.desc()
            ).label('rn')
        )
        .join(Build, Installation.build_id == Build.id)
        .join(Host, Installation.host_id == Host.id)
        .where(
            Installation.valid_to == None,
            Host.facility_id == facility.id
        )
        .subquery()
    )
    
    query = select(Installation).options(
        selectinload(Installation.user),
        selectinload(Installation.host),
        selectinload(Installation.build)
        .selectinload(Build.repository)
    ).join(Host, Installation.host_id == Host.id)
    
    if mode == "status":
        query = query.join(
            latest_subq,
            and_(Installation.id == latest_subq.c.id, latest_subq.c.rn == 1)
        )
    elif mode == "diff":
        query = query.join(
            latest_subq,
            and_(Installation.id == latest_subq.c.id, latest_subq.c.rn == 1)
        ).where(Installation.type == InstallationType.HOST)
    else:
        query = query.where(Host.facility_id == facility.id)
    
    query = query.order_by(Installation.install_date.desc())
    installations = session.exec(query).all()
    
    data = []
    for i in installations:
        data.append({
            "host": i.host.name,
            "repository": i.build.repository.name,
            "tag": i.build.tag,
            "date": i.install_date,
            "author": i.user.name
        })
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/facilities/{facility_name}/installations")
async def create_facility_installation(
    facility_name: str,
    req: InstallationRequest,
    username: str = Depends(authenticate),
    session: Session = Depends(get_session)
):
    """Installa su tutti gli host di una facility"""
    facility = session.exec(
        select(Facility).where(Facility.name == facility_name)
    ).first()
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    
    destinations = {}
    
    # Trova tutti i repository abilitati con questo nome
    repositories = session.exec(
        select(Repository)
        .where(Repository.name == req.repository, Repository.enabled == True)
    ).all()
    
    if not repositories:
        raise HTTPException(status_code=404, detail="Repository not found or not enabled")
    
    # Per ogni repository, trova gli host della facility
    for repo in repositories:
        servers = session.exec(
            select(Server).where(Server.platform_id == repo.platform_id)
        ).all()
        
        for server in servers:
            hosts = session.exec(
                select(Host)
                .where(
                    Host.server_id == server.id,
                    Host.facility_id == facility.id
                )
                .options(selectinload(Host.facility))
            ).all()
            if hosts:
                destinations[server] = hosts
    
    return install(username, req.repository, req.tag, destinations, InstallationType.FACILITY, session)

@app.get("/v2/cs/facilities/{facility_name}/hosts/{host_name}/installations")
async def get_host_installations(
    facility_name: str,
    host_name: str,
    mode: str = Query("status", regex="^(status|diff|history)$"),
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista le installazioni di un host specifico"""
    host = session.exec(
        select(Host)
        .join(Facility)
        .where(
            Facility.name == facility_name,
            Host.name == host_name
        )
    ).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    
    # Subquery per le ultime installazioni
    latest_subq = (
        select(
            Installation.id,
            func.row_number().over(
                partition_by=Build.repository_id,
                order_by=Installation.id.desc()
            ).label('rn')
        )
        .join(Build, Installation.build_id == Build.id)
        .where(
            Installation.valid_to == None,
            Installation.host_id == host.id
        )
        .subquery()
    )
    
    query = select(Installation).options(
        selectinload(Installation.user),
        selectinload(Installation.build)
        .selectinload(Build.repository)
    ).where(Installation.host_id == host.id)
    
    if mode == "status":
        query = query.join(
            latest_subq,
            and_(Installation.id == latest_subq.c.id, latest_subq.c.rn == 1)
        )
    elif mode == "diff":
        query = query.join(
            latest_subq,
            and_(Installation.id == latest_subq.c.id, latest_subq.c.rn == 1)
        ).where(Installation.type == InstallationType.HOST)
    
    query = query.order_by(Installation.install_date.desc())
    installations = session.exec(query).all()
    
    data = []
    for i in installations:
        data.append({
            "repository": i.build.repository.name,
            "tag": i.build.tag,
            "date": i.install_date,
            "author": i.user.name
        })
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/facilities/{facility_name}/hosts/{host_name}/installations")
async def create_host_installation(
    facility_name: str,
    host_name: str,
    req: InstallationRequest,
    username: str = Depends(authenticate),
    session: Session = Depends(get_session)
):
    """Installa su un host specifico"""
    host = session.exec(
        select(Host)
        .join(Facility)
        .where(
            Facility.name == facility_name,
            Host.name == host_name
        )
        .options(
            selectinload(Host.facility),
            selectinload(Host.server)
        )
    ).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    
    destinations = {host.server: [host]}
    
    return install(username, req.repository, req.tag, destinations, InstallationType.HOST, session)

# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "service": "inau-restapi"}

# Providers endpoints (mancanti nel vecchio codice ma necessari)

@app.get("/v2/cs/providers")
async def get_providers(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutti i providers"""
    providers = session.exec(select(Provider)).all()
    
    data = [{"id": p.id, "url": p.url} for p in providers]
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/providers", status_code=201)
async def create_provider(
    provider: ProviderRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea un nuovo provider (richiede admin)"""
    db_provider = Provider(url=provider.url)
    session.add(db_provider)
    
    try:
        session.commit()
        session.refresh(db_provider)
        return {"id": db_provider.id, "url": db_provider.url}
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Provider already exists")

# Servers endpoints

@app.get("/v2/cs/servers", response_model=List[ServerResponse])
async def get_servers(
    session: Session = Depends(get_session),
    accept: str = Header("application/json")
):
    """Lista tutti i servers"""
    servers = session.exec(
        select(Server)
        .options(
            selectinload(Server.platform)
            .selectinload(Platform.distribution),
            selectinload(Server.platform)
            .selectinload(Platform.architecture)
        )
    ).all()
    
    data = []
    for s in servers:
        data.append({
            "name": s.name,
            "prefix": s.prefix,
            "distribution": s.platform.distribution.name,
            "version": s.platform.distribution.version,
            "architecture": s.platform.architecture.name
        })
    
    if "text/plain" in accept:
        return PlainTextResponse(format_plain_text_response(data))
    
    return data

@app.post("/v2/cs/servers", response_model=ServerResponse, status_code=201)
async def create_server(
    server: ServerRequest,
    username: str = Depends(lambda: authenticate(AuthenticationType.ADMIN)),
    session: Session = Depends(get_session)
):
    """Crea un nuovo server (richiede admin)"""
    # Trova la piattaforma
    dist = session.exec(
        select(Distribution).where(
            Distribution.name == server.distribution,
            Distribution.version == server.version
        )
    ).first()
    if not dist:
        raise HTTPException(status_code=404, detail="Distribution not found")
    
    arch = session.exec(
        select(Architecture).where(Architecture.name == server.architecture)
    ).first()
    if not arch:
        raise HTTPException(status_code=404, detail="Architecture not found")
    
    platform = session.exec(
        select(Platform).where(
            Platform.distribution_id == dist.id,
            Platform.architecture_id == arch.id
        )
    ).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")
    
    db_server = Server(
        name=server.name,
        prefix=server.prefix,
        platform_id=platform.id
    )
    session.add(db_server)
    
    try:
        session.commit()
        session.refresh(db_server)
        return {
            "name": db_server.name,
            "prefix": db_server.prefix,
            "distribution": dist.name,
            "version": dist.version,
            "architecture": arch.name
        }
    except Exception:
        session.rollback()
        raise HTTPException(status_code=422, detail="Server already exists")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
