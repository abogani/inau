"""
INAU Models
Definizioni delle tabelle SQLModel e Enum condivisi
"""
from datetime import datetime
from typing import Optional, List
from enum import IntEnum
from sqlmodel import Field, SQLModel, Relationship


# Enum per i tipi
class RepositoryType(IntEnum):
    """Tipi di repository supportati"""
    CPLUSPLUS = 0
    PYTHON = 1
    CONFIGURATION = 2
    SHELLSCRIPT = 3
    LIBRARY = 4


class BuildStatus(IntEnum):
    """Stati possibili per una build"""
    SCHEDULED = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    CANCELLED = 4


class InstallationType(IntEnum):
    """Tipi di installazione"""
    GLOBAL = 0
    FACILITY = 1
    HOST = 2


class AuthenticationType(IntEnum):
    """Tipi di autenticazione"""
    USER = 0
    ADMIN = 1


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
    """Piattaforme di build"""
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
    builders: List["Builder"] = Relationship(back_populates="platform")


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
    type: int = Field(description="Repository type (see RepositoryType enum)")
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


class Builder(SQLModel, table=True):
    """Builder per compilare su diverse piattaforme"""
    __tablename__ = "builders"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    platform_id: int = Field(foreign_key="platforms.id", index=True)
    name: str = Field(max_length=255)
    environment: Optional[str] = Field(default=None, max_length=255)
    
    # Relationships
    platform: Platform = Relationship(back_populates="builders")


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
    type: int = Field(description="Installation type (see InstallationType enum)")
    install_date: datetime = Field(index=True)
    valid_from: datetime = Field(default_factory=datetime.utcnow, index=True)
    valid_to: Optional[datetime] = Field(default=None, index=True)
    
    # Relationships
    host: Host = Relationship(back_populates="installations")
    user: User = Relationship(back_populates="user")
    build: Build = Relationship(back_populates="installations")
