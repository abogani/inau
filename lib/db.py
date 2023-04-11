from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, func
from sqlalchemy.orm import relationship
from enum import Enum, IntEnum
import datetime

Base = declarative_base()

class Users(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    admin = Column(Boolean, default=False, nullable=False)
    notify = Column(Boolean, default=False, nullable=False)

class Architectures(Base):
    __tablename__ = 'architectures'
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    platforms = relationship('Platforms', back_populates='architecture')

class Distributions(Base):
    __tablename__ = 'distributions'
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    version = Column(String(255), nullable=False)
    platforms = relationship('Platforms', back_populates='distribution')

class Platforms(Base):
    __tablename__ = 'platforms'
    id = Column(Integer, primary_key=True)
    distribution_id = Column(Integer, ForeignKey('distributions.id'), nullable=False)
    architecture_id = Column(Integer, ForeignKey('architectures.id'), nullable=False)
    architecture = relationship('Architectures', back_populates='platforms')
    distribution = relationship('Distributions', back_populates='platforms')
#    servers = relationship('Servers', back_populates='platform')
#
#class Facilities(Base):
#    __tablename__ = 'facilities'
#    id = Column(Integer, primary_key=True)
#    name = Column(String(255), unique=True, nullable=False)
##    hosts = relationship('Hosts', back_populates='server')
#
#class Servers(Base):
#    __tablename__ = 'servers'
#    id = Column(Integer, primary_key=True)
#    platform_id = Column(Integer, ForeignKey('platforms.id'), nullable=False)
#    name = Column(String(255), nullable=False)
#    prefix = Column(String(255), nullable=False)
#    platform = relationship('Platforms', back_populates='servers')
##    hosts = relationship('Hosts', back_populates='server')
#
#class Hosts(Base):
#    __tablename__ = 'hosts'
#    id = Column(Integer, primary_key=True)
#    facility_id = Column(Integer, ForeignKey('facilities.id'), nullable=False)
#    server_id = Column(Integer, ForeignKey('servers.id'), nullable=False)
#    name = Column(String(255), unique=True, nullable=False)
##    facility = relationship('Facilities', back_populates='hosts')
##    server = relationship('Servers', back_populates='hosts')
#
###################################################################################################
#
class Builders(Base):
    __tablename__ = 'builders'

    id = Column(Integer, primary_key=True)
    platform_id = Column(Integer, ForeignKey('platforms.id'), nullable=False)
    name = Column(String(255), unique=False, nullable=False)

class Providers(Base):
    __tablename__ = 'providers'

    id = Column(Integer, primary_key=True)
    url = Column(String(255), unique=True, nullable=False)
#    repositories = relationship('Repositories', back_populates='provider')

class RepositoryType(IntEnum):
    cplusplus = 0,
    python = 1,
    configuration = 2,
    shellscript = 3

class Repositories(Base):
    __tablename__ = 'repositories'

    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey('providers.id'), nullable=False)
    platform_id = Column(Integer, ForeignKey('platforms.id'), nullable=False)
    type = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    destination = Column(String(255), nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
#    builds = relationship('Builds', back_populates='repository')
#    provider = relationship('Providers', back_populates='repositories')

class Builds(Base):
    __tablename__ = 'builds'

    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey('repositories.id'), nullable=False)
    platform_id = Column(Integer, ForeignKey('platforms.id'), nullable=False)
    tag = Column(String(255), nullable=False)
    date = Column(DateTime, default=datetime.datetime.now, nullable=False)
    status = Column(Integer, nullable=True)
    output = Column(Text, nullable=True)
#    repository = relationship('Repositories', back_populates='builds')
##    platform = relationship('Platforms', back_populates='builds')

class Artifacts(Base):
    __tablename__ = 'artifacts'

    id = Column(Integer, primary_key=True)
    build_id = Column(Integer, ForeignKey('builds.id'), nullable=False)
    hash = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
#    build = db.relationship('Builds', lazy=True, backref=db.backref('artifacts', lazy=True))

##class Installations(db.Model):
##    id = db.Column(db.Integer, primary_key=True)
##    host_id = db.Column(db.Integer, db.ForeignKey('hosts.id'), nullable=False)
##    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
##    build_id = db.Column(db.Integer, db.ForeignKey('builds.id'), nullable=False)
##    type = db.Column(db.Integer, nullable=False)
##    date = db.Column(db.DateTime, nullable=False)
##    host = db.relationship('Hosts', lazy=True, backref=db.backref('installations', lazy=True))
##    user = db.relationship('Users', lazy=True, backref=db.backref('installations', lazy=True))
##    build = db.relationship('Builds', lazy=True, backref=db.backref('installations', lazy=True))
