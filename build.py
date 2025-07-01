"""
INAU Build Worker
Gestisce la compilazione dei progetti ricevuti tramite Celery
"""
import os
import sys
import subprocess
import hashlib
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from contextlib import contextmanager
import tempfile
import json

from celery import Celery, Task
from celery.utils.log import get_task_logger
from sqlmodel import Session, select, create_engine
from pydantic import BaseModel
import paramiko
import git

# Import dei modelli dal models.py
from models import (
    BuildStatus, RepositoryType, 
    Repository, Build, Artifact, Platform, Builder,
    Distribution, Architecture, User
)

# Configurazione database
DATABASE_URL = os.getenv('DATABASE_URL', None)
engine = create_engine(DATABASE_URL, echo=False)

# Configurazione Celery
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', None)
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', None)

# Configurazione paths
REPO_BASE_DIR = os.getenv('INAU_REPO_DIR', '/scratch/build/repositories')
STORE_BASE_DIR = os.getenv('INAU_STORE_DIR', '/scratch/build/files-store')
BUILD_TIMEOUT = int(os.getenv('INAU_BUILD_TIMEOUT', '3600'))  # 1 ora default

# Configurazione email
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.elettra.eu')
SMTP_DOMAIN = os.getenv('SMTP_DOMAIN', 'elettra.eu')
SMTP_SENDER = os.getenv('SMTP_SENDER', 'noreply')

# Setup Celery
app = Celery('inau.build', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

# Configurazione Celery
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=BUILD_TIMEOUT + 300,  # Hard limit
    task_soft_time_limit=BUILD_TIMEOUT,    # Soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=10,
)

# Logger
logger = get_task_logger(__name__)

class BuildTask(BaseModel):
    """Messaggio di build da webhook"""
    build_id: int
    repository_id: int
    platform_id: int
    tag: str
    repository_name: str
    repository_url: str
    repository_type: int
    user_email: Optional[str] = None
    default_branch: str = "master"
    emails: List[str] = []

class BuildWorker:
    """Gestisce il processo di build per una piattaforma specifica"""
    
    def __init__(self, platform_id: int):
        self.platform_id = platform_id
        self.platform_dir = Path(REPO_BASE_DIR) / str(platform_id)
        self.platform_dir.mkdir(parents=True, exist_ok=True)
        
    @contextmanager
    def get_session(self):
        """Context manager per la sessione del database"""
        with Session(engine) as session:
            yield session
            
    def get_builder(self, session: Session) -> Optional[Builder]:
        """Ottiene il builder per la piattaforma corrente"""
        return session.exec(
            select(Builder).where(Builder.platform_id == self.platform_id)
        ).first()
        
    def update_repository(self, task: BuildTask) -> Tuple[bool, str]:
        """Aggiorna o clona il repository"""
        repo_path = self.platform_dir / task.repository_name
        
        try:
            # Aggiorna makefiles se necessario
            makefiles_path = self.platform_dir / "cs/ds/makefiles"
            if not makefiles_path.exists():
                logger.info(f"Cloning makefiles repository...")
                git.Repo.clone_from(
                    "https://gitlab.elettra.eu/cs/ds/makefiles.git",
                    makefiles_path,
                    recurse_submodules='true'
                )
            else:
                logger.info(f"Updating makefiles repository...")
                makefiles_repo = git.Repo(makefiles_path)
                makefiles_repo.remotes.origin.fetch()
                makefiles_repo.git.reset('--hard', 'origin/master')
            
            # Gestione del repository del progetto
            if repo_path.exists():
                logger.info(f"Updating repository {task.repository_name}...")
                repo = git.Repo(repo_path)
                repo.remotes.origin.fetch(tags=True)
                repo.git.pull('--tags')
            else:
                logger.info(f"Cloning repository {task.repository_name}...")
                repo = git.Repo.clone_from(
                    task.repository_url,
                    repo_path,
                    recurse_submodules='true'
                )
                
            # Verifica che il tag sia nel branch di default
            branches = repo.git.branch('--no-color', '--contains', task.tag).split('\n')
            if not any(task.default_branch in branch for branch in branches):
                return False, f"Tag {task.tag} not found in default branch {task.default_branch}"
                
            # Checkout del tag
            logger.info(f"Checking out tag {task.tag}...")
            repo.git.reset('--hard', task.tag, '--')
            repo.git.submodule('update', '--init', '--force', '--recursive')
            
            return True, "Repository updated successfully"
            
        except Exception as e:
            logger.error(f"Error updating repository: {str(e)}")
            return False, str(e)
            
    def build_on_builder(self, builder: Builder, task: BuildTask) -> Tuple[int, str]:
        """Esegue la build su un builder remoto"""
        repo_path = self.platform_dir / task.repository_name
        
        try:
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(
                    hostname=builder.name,
                    port=22,
                    username="inau",
                    key_filename=os.path.expanduser("~/.ssh/id_rsa")
                )
                
                # Prepara il comando di build
                environment = builder.environment or ""
                if environment:
                    environment = f"source {environment}; "
                    
                base_cmd = f"{environment}source /etc/profile; cd {repo_path}"
                
                if task.repository_type == RepositoryType.LIBRARY:
                    build_cmd = (
                        f"{base_cmd}; "
                        f"make -j$(getconf _NPROCESSORS_ONLN) && "
                        f"rm -fr .install && "
                        f"PREFIX=.install make install"
                    )
                else:
                    build_cmd = f"{base_cmd}; make -j$(getconf _NPROCESSORS_ONLN)"
                    
                logger.info(f"Executing build command on {builder.name}...")
                stdin, stdout, stderr = ssh.exec_command(f"({build_cmd}) 2>&1")
                
                # Attendi il completamento
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode('utf-8', errors='replace')
                
                return exit_status, output
                
        except Exception as e:
            logger.error(f"SSH error: {str(e)}")
            return -1, str(e)
            
    def collect_artifacts(self, task: BuildTask, build: Build, session: Session) -> List[Artifact]:
        """Raccoglie e salva gli artifacts prodotti dalla build"""
        repo_path = self.platform_dir / task.repository_name
        artifacts = []
        
        # Determina la directory base per gli artifacts
        if task.repository_type == RepositoryType.CPLUSPLUS:
            base_dirs = [repo_path / "bin"]
        elif task.repository_type == RepositoryType.PYTHON:
            base_dirs = [repo_path / "bin"]
        elif task.repository_type == RepositoryType.SHELLSCRIPT:
            base_dirs = [repo_path / "bin"]
        elif task.repository_type == RepositoryType.CONFIGURATION:
            base_dirs = [repo_path / "etc"]
        elif task.repository_type == RepositoryType.LIBRARY:
            base_dirs = [repo_path / ".install"]
        else:
            logger.warning(f"Unknown repository type: {task.repository_type}")
            return artifacts
            
        for base_dir in base_dirs:
            if not base_dir.exists():
                continue
                
            for file_path in base_dir.rglob('*'):
                if file_path.is_file():
                    relative_path = file_path.relative_to(base_dir)
                    
                    if file_path.is_symlink():
                        # Gestione symlink
                        target = os.readlink(file_path)
                        artifact = Artifact(
                            build_id=build.id,
                            build_date=build.date,
                            filename=str(relative_path),
                            symlink_target=target
                        )
                    else:
                        # File normale - calcola hash e salva
                        file_hash = self._hash_and_store_file(file_path)
                        artifact = Artifact(
                            build_id=build.id,
                            build_date=build.date,
                            hash=file_hash,
                            filename=str(relative_path)
                        )
                    
                    artifacts.append(artifact)
                    
        # Salva tutti gli artifacts nel database
        session.add_all(artifacts)
        session.commit()
        
        return artifacts
        
    def _hash_and_store_file(self, file_path: Path) -> str:
        """Calcola l'hash SHA256 del file e lo salva nello store"""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
                
        file_hash = sha256_hash.hexdigest()
        
        # Crea la struttura di directory per lo store
        hash_dir = Path(STORE_BASE_DIR) / file_hash[:2] / file_hash[2:4]
        hash_dir.mkdir(parents=True, exist_ok=True)
        
        # Salva il file se non esiste già
        store_path = hash_dir / file_hash
        if not store_path.exists():
            shutil.copy2(file_path, store_path)
            
        return file_hash
        
    def send_notification(self, task: BuildTask, build: Build, success: bool):
        """Invia notifica email del risultato della build"""
        try:
            from smtplib import SMTP
            from email.mime.text import MIMEText
            
            subject = f"INAU Build {'Success' if success else 'Failed'}: {task.repository_name} {task.tag}"
            
            if success:
                body = f"Build completed successfully for {task.repository_name} tag {task.tag}\n\n"
            else:
                body = f"Build failed for {task.repository_name} tag {task.tag}\n\n"
                
            body += f"Platform: {self.platform_id}\n"
            body += f"Date: {build.date}\n\n"
            
            if build.output:
                body += "Build output:\n"
                body += "-" * 60 + "\n"
                body += build.output[-5000:]  # Ultimi 5000 caratteri
                
            # Determina i destinatari
            recipients = set()
            
            # Aggiungi le email dal task
            for email in task.emails:
                if email and '@' in email:
                    recipients.add(email)
                    
            if task.user_email and '@' in task.user_email:
                recipients.add(task.user_email)
                
            with Session(engine) as session:
                # Aggiungi utenti con notifiche abilitate
                notifiable_users = session.exec(
                    select(User).where(User.notify == True)
                ).all()
                
                for user in notifiable_users:
                    recipients.add(f"{user.name}@{SMTP_DOMAIN}")
                    
            if recipients:
                msg = MIMEText(body)
                msg['Subject'] = subject
                msg['From'] = f"{SMTP_SENDER}@{SMTP_DOMAIN}"
                msg['To'] = ', '.join(recipients)
                
                with SMTP(SMTP_SERVER, 25) as smtp:
                    smtp.send_message(msg)
                    
        except Exception as e:
            logger.error(f"Failed to send notification: {str(e)}")

@app.task(bind=True, name='inau.build.process_build')
def process_build(self: Task, build_data: dict) -> dict:
    """Task Celery per processare una build"""
    task = BuildTask(**build_data)
    worker = BuildWorker(task.platform_id)
    
    logger.info(f"Starting build {task.build_id} for {task.repository_name} on platform {task.platform_id}")
    
    with worker.get_session() as session:
        # Aggiorna lo stato della build
        build = session.get(Build, task.build_id)
        if not build:
            logger.error(f"Build {task.build_id} not found")
            return {"success": False, "error": "Build not found"}
            
        build.status = BuildStatus.RUNNING
        session.commit()
        
        try:
            # Ottieni il builder per questa piattaforma
            builder = worker.get_builder(session)
            if not builder:
                raise Exception(f"No builder found for platform {task.platform_id}")
                
            # Aggiorna il repository
            success, message = worker.update_repository(task)
            if not success:
                raise Exception(f"Repository update failed: {message}")
                
            # Esegui la build
            exit_status, output = worker.build_on_builder(builder, task)
            
            # Aggiorna il risultato della build
            build.status = BuildStatus.SUCCESS if exit_status == 0 else BuildStatus.FAILED
            build.output = output
            session.commit()
            
            # Se la build è riuscita, raccogli gli artifacts
            artifacts = []
            if exit_status == 0:
                artifacts = worker.collect_artifacts(task, build, session)
                logger.info(f"Collected {len(artifacts)} artifacts")
                
            # Invia notifica
            worker.send_notification(task, build, exit_status == 0)
            
            return {
                "success": exit_status == 0,
                "build_id": task.build_id,
                "exit_status": exit_status,
                "artifacts_count": len(artifacts) if exit_status == 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Build failed with error: {str(e)}")
            build.status = BuildStatus.FAILED
            build.output = str(e)
            session.commit()
            
            worker.send_notification(task, build, False)
            
            return {
                "success": False,
                "build_id": task.build_id,
                "error": str(e)
            }
