import logging
import argparse
import datetime
import time
import ldap
import base64
import json
import paramiko
import shutil
import hashlib
import os
import git
import re
from enum import Enum, IntEnum
from werkzeug.exceptions import HTTPException, Unauthorized, Forbidden, InternalServerError, MethodNotAllowed, BadRequest, UnprocessableEntity, NotFound
from smtplib import SMTP
from email.mime.text import MIMEText
from flask import Flask, request, make_response, got_request_exception, render_template
from flask_restful import Resource, Api, reqparse, fields, marshal_with
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError

# Create Flask, Api and SQLAlchemy object
app = Flask(__name__)
v2 = Api(app, prefix='/v2') # default_mediatype doesn't take "Accept: */*" into account
db = SQLAlchemy()

parser = argparse.ArgumentParser()
parser.add_argument("--db", required=True)
parser.add_argument("--smtpdomain", default="elettra.eu")
parser.add_argument("--smtpserver", default="smtp.elettra.eu")
parser.add_argument("--smtpsender", default="noreply")
parser.add_argument("--store", default="/scratch/build/files-store/")
parser.add_argument("--repo", default="/scratch/build/repositories/")
parser.add_argument("--ldap", default="ldaps://abook.elettra.eu:636")
parser.add_argument("--port", default="443")
args = parser.parse_args()

class Users(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    admin = db.Column(db.Boolean, nullable=False)

class Facilities(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)

class Distributions(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    version = db.Column(db.String(255), nullable=False)

class Architectures(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)

class Platforms(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    distribution_id = db.Column(db.Integer, db.ForeignKey('distributions.id'), nullable=False)
    architecture_id = db.Column(db.Integer, db.ForeignKey('architectures.id'), nullable=False)
    distribution = db.relationship('Distributions', lazy=True, backref=db.backref('platforms', lazy=True))
    architecture = db.relationship('Architectures', lazy=True, backref=db.backref('platforms', lazy=True))

class Providers(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(255), unique=True, nullable=False)

class Repositories(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('providers.id'), nullable=False)
    platform_id = db.Column(db.Integer, db.ForeignKey('platforms.id'), nullable=False)
    type = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(255), unique=True, nullable=False)
    destination = db.Column(db.String(255), nullable=False)
    provider = db.relationship('Providers', lazy=True, backref=db.backref('repositories', lazy=True))
    platform = db.relationship('Platforms', lazy=True, backref=db.backref('repositories', lazy=True))
    enabled = db.Column(db.Boolean, default=True, nullable=False)

class Servers(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    platform_id = db.Column(db.Integer, db.ForeignKey('platforms.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    prefix = db.Column(db.String(255), nullable=False)
    platform = db.relationship('Platforms', lazy=True, backref=db.backref('servers', lazy=True))

class Hosts(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facilities.id'), nullable=False)
    server_id = db.Column(db.Integer, db.ForeignKey('servers.id'), nullable=False)
    name = db.Column(db.String(255), unique=True, nullable=False)
    facility = db.relationship('Facilities', lazy=True, backref=db.backref('hosts', lazy=True))
    server = db.relationship('Servers', lazy=True, backref=db.backref('hosts', lazy=True))

class Builders(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    platform_id = db.Column(db.Integer, db.ForeignKey('platforms.id'), nullable=False)
    name = db.Column(db.String(255), unique=False, nullable=False)
    platform = db.relationship('Platforms', lazy=True, backref=db.backref('builders', lazy=True))
    environment = db.Column(db.String(255), unique=False, nullable=True)

class Artifacts(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    build_id = db.Column(db.Integer, db.ForeignKey('builds.id'), nullable=False)
    hash = db.Column(db.String(255), nullable=True)
    filename = db.Column(db.String(255), nullable=False)
    symlink_target = db.Column(db.String(255), nullable=True)
    build = db.relationship('Builds', lazy=True, backref=db.backref('artifacts', lazy=True))

class Builds(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    repository_id = db.Column(db.Integer, db.ForeignKey('repositories.id'), nullable=False)
    platform_id = db.Column(db.Integer, db.ForeignKey('platforms.id'), nullable=False)
    tag = db.Column(db.String(255), nullable=False)
    date = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    status = db.Column(db.Integer, nullable=True)
    output = db.Column(db.Text, nullable=True)
    repository = db.relationship('Repositories', lazy=True, backref=db.backref('builds', lazy=True))
#    platform = db.relationship('Platforms', lazy=True, backref=db.backref('repositories', lazy=True))

class Installations(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    host_id = db.Column(db.Integer, db.ForeignKey('hosts.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    build_id = db.Column(db.Integer, db.ForeignKey('builds.id'), nullable=False)
    type = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    host = db.relationship('Hosts', lazy=True, backref=db.backref('installations', lazy=True))
    user = db.relationship('Users', lazy=True, backref=db.backref('installations', lazy=True))
    build = db.relationship('Builds', lazy=True, backref=db.backref('installations', lazy=True))

class AuthenticationType(Enum):
    USER = 0,
    ADMIN = 1

def authenticate(authtype, request):
    auth = ldap.initialize(app.config['LDAP_URL'], bytes_mode=False)
    if request.headers.get('Authorization') == None:
        print("Missing authorization header")
        raise Unauthorized()
    split = request.headers.get('Authorization').strip().split(' ')
    username, password = base64.b64decode(split[1]).decode().split(':', 1)
    user = Users.query.filter(Users.name == username).first()
    if user is None:
        print("User isn't enabled")
        raise Forbidden()
    if authtype == AuthenticationType.ADMIN and user.admin is False:
        print("Admin authentication type is required")
        raise Forbidden()
    try:
        auth.simple_bind_s("uid=" + username +",ou=people,dc=elettra,dc=eu", password)
        auth.unbind_s()
    except Exception as e:
        print("LDAP issue: ", e)
        raise Forbidden()
    return username

@v2.representation('application/json')
def output_json(data, code, headers=None):
    resp = make_response(json.dumps(data), code)
    resp.headers.extend(headers or {})
    return resp

@v2.representation('text/plain')
def output_plain(data, code, headers=None):
    retval = ""
    highest = {}

    if isinstance(data, dict):
        try:
            message = data['message']
            if isinstance(message, dict):
                for k, v in message.items():
                    retval += "message: " + v + "\n"
            else: # str
                retval += "message: " + message + "\n";
        except KeyError:
            data = [data]

    if isinstance(data, list):
        for item in data:
            for k, v in item.items():
                highest.update({ k : max(highest.get(k, 0), len(k), len(str(v))) })
      
        if len(data):
            columns = ""
            for k, v in data[0].items():
                if len(columns) != 0:
                    columns += "  "
                columns += k.ljust(highest[k])
            retval += columns + "\n"

            line = ""
            for k, v in data[0].items():
                if len(line) != 0:
                    line += "--"
                line += "-" * highest[k]
            retval += line + "\n"

        for item in data:
            rows = ""
            for k, v in item.items():
                if len(rows) != 0:
                    rows += "  "
                rows += str(v).ljust(highest[k])
            retval += rows + "\n"

    retval = retval[:-1]

    resp = make_response(retval, code)
    resp.headers.extend(headers or {})
    return resp

def non_empty_string(s):
    if not s:
        raise ValueError("Must not be empty string")
    return s

def execSyncedCommand(sshClient, cmd):
    _, stdout, stderr = sshClient.exec_command(cmd)
    exitStatus = stdout.channel.recv_exit_status()
    return stdout.read().decode('utf-8'), stderr.read().decode('utf-8'), exitStatus

def sendEmail(to, subject, body):
    if args.port != "443":
        print(subject, str(body))
    else:
        with SMTP(host=app.config['MAIL_SERVER'], port=25) as smtpClient:
            sender = app.config['MAIL_DEFAULT_SENDER']
            receivers = [ to ]
            msg = MIMEText(str(body))
            msg['Subject'] = "INAU. " + subject
            msg['From'] = sender
            msg['To'] = to[0]
            smtpClient.sendmail(from_addr=sender, to_addrs=receivers,
                    msg=msg.as_string())

def sendEmailAdmins(subject, body):
    for admin in Users.query.filter(Users.admin == True).all():
        sendEmail([admin.name + "@" + app.config['MAIL_DOMAIN']], subject, body)

def log_exception(sender, exception, **extra):
    if isinstance(exception, MethodNotAllowed) or isinstance(exception, BadRequest) or \
            isinstance(exception, UnprocessableEntity) or isinstance(exception, Forbidden):
        return
    sendEmailAdmins(str(sender), str(exception))

got_request_exception.connect(log_exception, app)

class InstallationType(IntEnum):
    GLOBAL = 0,
    FACILITY = 1,
    HOST = 2

class RepositoryType(IntEnum):
    cplusplus = 0,
    python = 1,
    configuration = 2,
    shellscript = 3,
    library = 4

def install(username, reponame, tag, destinations, itype):
    now = datetime.datetime.now()
    retval = []
    user = Users.query.filter(Users.name == username) \
            .first_or_404(description='User not found')
    if not destinations.items():
        raise NotFound
    for server, hosts in destinations.items():
        repository = Repositories.query.with_parent(server.platform) \
                .filter(Repositories.name == reponame) \
                .first_or_404(description='Repository not found. Check syntax.')
        build = Builds.query.with_parent(repository).filter(Builds.tag == tag, Builds.status == 0) \
                .order_by(Builds.id.desc()) \
                .first_or_404("Requested build not available. Check annotated tag.")
        try:
            with paramiko.SSHClient() as sshClient:
                sshClient.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                print("Connect to " + server.name + "...") 
                sshClient.connect(hostname=server.name, port=22, username="root",
                        key_filename="/home/inau/.ssh/id_rsa.pub")
                with sshClient.open_sftp() as sftpClient:
                    artifacts = []
                    for host in hosts:
                        facility = host.facility.name
                    for artifact in Artifacts.query.with_parent(build).all():
                        if repository.type == RepositoryType.library and facility != "development":
                            if re.search("^(lib/|bin/)", artifact.filename):
                                if re.search("^(lib/cmake|lib/pkgconfig)", artifact.filename):
                                    pass
                                else:
                                   artifacts.append(artifact)
                            else:
                                pass
                        else:
                            artifacts.append(artifact)
                    for artifact in artifacts:
                        print("Install", artifact.filename, "to", server.name, "...")
                        if artifact.hash is not None:
                            with open(app.config['FILES_STORE_DIR'] + artifact.hash[0:2] + "/" + artifact.hash[2:4] + "/" + artifact.hash, "rb") as binaryFile:
                                    sftpClient.putfo(binaryFile, "/tmp/" + artifact.hash)
                                    filemode = "755"
                                    if repository.type == RepositoryType.configuration:
                                        filemode = "644"
                                    # repository.type == RepositoryType.library have to provide files with the right permissions
                                    if itype == InstallationType.GLOBAL or itype == InstallationType.FACILITY:
                                        if not repository.type == RepositoryType.library:
                                            cmd = "rm " + server.prefix + "/site/*/" + repository.destination + artifact.filename
                                            _, _, _ = execSyncedCommand(sshClient, cmd)
                                            cmd = "install -d " + server.prefix + repository.destination \
                                                    + os.path.dirname(artifact.filename)
                                            _, _, _ = execSyncedCommand(sshClient, cmd)
                                            cmd = "install -m" + filemode + " /tmp/" + artifact.hash + " " \
                                                    + server.prefix + repository.destination + artifact.filename
                                            _, stderr, exitStatus = execSyncedCommand(sshClient, cmd)
                                        else:
                                            cmd = "rm " + server.prefix + "/site/*/" + artifact.filename
                                            _, _, _ = execSyncedCommand(sshClient, cmd)
                                            cmd = "install -d " + server.prefix + os.path.dirname(artifact.filename)
                                            _, _, _ = execSyncedCommand(sshClient, cmd)
                                            cmd = "install " + "/tmp/" + artifact.hash + " " \
                                                    + server.prefix + artifact.filename
                                            _, stderr, exitStatus = execSyncedCommand(sshClient, cmd)
                                        if exitStatus != 0:
                                            raise Exception(stderr)
                                    else: # InstallationType.HOST
                                        for host in hosts:
                                            if not repository.type == RepositoryType.library:
                                                cmd = "install -d " + server.prefix + "/site/" + host.name + "/" \
                                                        + repository.destination + os.path.dirname(artifact.filename)
                                                _, _, _ = execSyncedCommand(sshClient, cmd)
                                                cmd =  "install -m" + filemode + " /tmp/" + artifact.hash + " " + server.prefix \
                                                    + "/site/" + host.name  + "/" + repository.destination + artifact.filename
                                                _, stderr, exitStatus = execSyncedCommand(sshClient, cmd)
                                            else:
                                                cmd = "install -d " + server.prefix + "/site/" + host.name + "/" \
                                                        + os.path.dirname(artifact.filename)
                                                _, _, _ = execSyncedCommand(sshClient, cmd)
                                                cmd =  "install " + "/tmp/" + artifact.hash + " " + server.prefix \
                                                    + "/site/" + host.name  + "/" + artifact.filename
                                                _, stderr, exitStatus = execSyncedCommand(sshClient, cmd)
                                            if exitStatus != 0:
                                                raise Exception(stderr)
                        else:
                            if itype == InstallationType.GLOBAL or itype == InstallationType.FACILITY:
                                cmd = "rm " + server.prefix + "/site/*/" + artifact.filename
                                _, _, _ = execSyncedCommand(sshClient, cmd)
                                cmd = "ln -sfrn " + server.prefix + artifact.symlink_target  + " " + server.prefix + artifact.filename
                            else:
                                cmd = "ln -sfrn " + server.prefix + "/site/" + host.name + "/" + artifact.symlink_target \
                                        + " " + server.prefix + "/site/" + host.name + "/" + artifact.filename
                            _, stderr, exitStatus = execSyncedCommand(sshClient, cmd)
                            if exitStatus != 0:
                                raise Exception(stderr)
        except Exception as e:
            raise InternalServerError(description=str(e))

        for host in hosts:
            installation = Installations(user=user, host=host, build=build, date=now, type=int(itype))
            db.session.add(installation)
            db.session.commit()
                
            retval.append({ 'facility': host.facility.name, 'host': host.name,
                'repository': repository.name,'tag': build.tag, 'date': installation.date,
                'author': user.name })
    return retval

class CSHandler(Resource):
    def get(self):
        return [{ 'subpath': 'users'},
                { 'subpath': 'distributions'},
                { 'subpath': 'architectures'},
                { 'subpath': 'platforms'},
                { 'subpath': 'builders'},
                { 'subpath': 'servers'},
                { 'subpath': 'providers'},
                { 'subpath': 'repositories'},
                { 'subpath': 'facilities' }]

users_fields = { 'name': fields.String() }
class UsersHandler(Resource):
    @marshal_with(users_fields)
    def get(self):
        users = Users.query.all()
        return users, 200 if users else 204
    @marshal_with(users_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=alessio.bogani)')
        args = parser.parse_args(strict=True)
        args = parser.parse_args()
        authenticate(AuthenticationType.ADMIN, request)
        try:
            user = Users(name = args['name'], admin = False)
            db.session.add(user)
            db.session.commit()
            return user, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class UserHandler(Resource):
    @marshal_with(users_fields)
    def put(self, username):
        user = Users.query.filter(Users.name == username).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default=user.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=alessio.bogani)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        user.name = args['name']
        db.session.commit()
        return user, 201
    def delete(self, username):
        user = Users.query.filter(Users.name == username).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(user)
        db.session.commit()
        return {}, 204

architectures_fields = { 'name': fields.String() }
class ArchitecturesHandler(Resource):
    @marshal_with(architectures_fields)
    def get(self):
        architectures = Architectures.query.all()
        return architectures, 200 if architectures else 204
    @marshal_with(architectures_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        try:
            arch = Architectures(name = args['name'])
            db.session.add(arch)
            db.session.commit()
            return arch, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class ArchitectureHandler(Resource):
    @marshal_with(architectures_fields)
    def put(self, archname):
        arch = Architectures.query.filter(Architectures.name == archname).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default=arch.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        arch.name = args['name']
        db.session.commit()
        return arch, 201
    def delete(self, archname):
        arch = Architectures.query.filter(Architectures.name == archname).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(arch)
        db.session.commit()
        return {}, 204

distributions_fields = { 'id': fields.Integer(), 
        'name': fields.String(), 'version': fields.String() }
class DistributionsHandler(Resource):
    @marshal_with(distributions_fields)
    def get(self):
        distributions = Distributions.query.all()
        return distributions, 200 if distributions else 204
    @marshal_with(distributions_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=Ubuntu)')
        parser.add_argument('version', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        try:
            distro = Distributions(name = args['name'], version = args['version'])
            db.session.add(distro)
            db.session.commit()
            return distro, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class DistributionHandler(Resource):
    @marshal_with(distributions_fields)
    def put(self, distroid):
        distro = Distributions.query.filter(Distributions.id == distroid).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default=distro.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=Ubuntu)')
        parser.add_argument('version', default=distro.version, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro.name = args['name']
        distro.version = args['version']
        db.session.commit()
        return distro, 201
    def delete(self, distroid):
        distro = Distributions.query.filter(Distributions.id == distroid).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(distro)
        db.session.commit()
        return {}, 204

platforms_fields = { 'id': fields.Integer,
        'distribution': fields.String(attribute='distribution.name'),
        'version': fields.String(attribute='distribution.version'),
        'architecture': fields.String(attribute='architecture.name') }
class PlatformsHandler(Resource):
    @marshal_with(platforms_fields)
    def get(self):
        platforms = Platforms.query.all()
        return platforms, 200 if platforms else 204
    @marshal_with(platforms_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('distribution', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. architecture=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        try:
            plat = Platforms(architecture_id = arch.id, distribution_id = distro.id)
            db.session.add(plat)
            db.session.commit()
            return plat, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class PlatformHandler(Resource):
    @marshal_with(platforms_fields)
    def put(self, platid):
        plat = Platforms.query.filter(Platforms.id == platid).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('distribution', default=plat.distribution.name, trim=True, 
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', default=plat.distribution.version, trim=True, 
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', default=plat.architecture.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. architecture=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        plat.distribution_id = distro.id
        plat.architecture_id = arch.id
        db.session.commit()
        return plat, 201
    def delete(self, platid):
        plat = Platforms.query.filter(Platforms.id == platid).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(plat)
        db.session.commit()
        return {}, 204

builders_fields = { 'name': fields.String(),
        'distribution': fields.String(attribute='platform.distribution.name'),
        'version': fields.String(attribute='platform.distribution.version'),
        'architecture': fields.String(attribute='platform.architecture.name') }
class BuildersHandler(Resource):
    @marshal_with(builders_fields)
    def get(self):
        builders = Builders.query.all()
        return builders, 200 if builders else 204
    @marshal_with(builders_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=anguilla)')
        parser.add_argument('distribution', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. architecture=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        plat = Platforms.query.with_parent(distro).with_parent(arch) \
                .first_or_404(description="Platform doesn't exist")
        try:
            builder = Builders(name = args['name'], platform = plat)
            db.session.add(builder)
            db.session.commit()
            return builder, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class BuilderHandler(Resource):
    @marshal_with(builders_fields)
    def put(self, buildername):
        builder = Builders.query.filter(Builders.name == buildername).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default=builder.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=anguilla)')
        parser.add_argument('distribution', default=builder.platform.distribution.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', default=builder.platform.distribution.version, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', default=builder.platform.architecture.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. architecture=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        plat = Platforms.query.with_parent(distro).with_parent(arch) \
                .first_or_404(description="Platform doesn't exist")
        builder.name = args['name']
        builder.platform = plat
        db.session.commit()
        return builder, 201
#    def delete(self, buildername):
#        builder = Builders.query.filter(Builders.name == buildername).first_or_404()
#        authenticate(AuthenticationType.ADMIN, request)
#        db.session.delete(builder)
#        db.session.commit()
#        return {}, 204

servers_fields = { 'name': fields.String(), 'prefix': fields.String(),
        'distribution': fields.String(attribute='platform.distribution.name'),
        'version': fields.String(attribute='platform.distribution.version'),
        'architecture': fields.String(attribute='platform.architecture.name') }
class ServersHandler(Resource):
    @marshal_with(servers_fields)
    def get(self):
        servers = Servers.query.all()
        return servers, 200 if servers else 204
    @marshal_with(servers_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=srv-net-srf)')
        parser.add_argument('prefix', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. prefix=/runtime/)')
        parser.add_argument('distribution', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. architecture=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        plat = Platforms.query.with_parent(distro).with_parent(arch) \
                .first_or_404(description="Platform doesn't exist")
        try:
            server = Servers(name = args['name'], prefix = args['prefix'], platform = plat)
            db.session.add(server)
            db.session.commit()
            return server, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class ServerHandler(Resource):
    @marshal_with(servers_fields)
    def put(self, servername):
        server = Servers.query.filter(Servers.name == servername).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default=server.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=srv-net-srf)')
        parser.add_argument('prefix', default=server.prefix, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. prefix=/runtime/)')
        parser.add_argument('distribution', default=server.platform.distribution.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', default=server.platform.distribution.version, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', default=server.platform.architecture.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. architecture=ppc7400)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        plat = Platforms.query.with_parent(distro).with_parent(arch) \
                .first_or_404(description="Platform doesn't exist")
        server.name = args['name']
        server.prefix = args['prefix']
        server.platform = plat
        db.session.commit()
        return server, 201
    def delete(self, servername):
        server = Servers.query.filter(Servers.name == servername).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(server)
        db.session.commit()
        return {}, 204

providers_fields = { 'id': fields.Integer(), 'url': fields.String() }
class ProvidersHandler(Resource):
    @marshal_with(providers_fields)
    def get(self):
        providers = Providers.query.all()
        return providers, 200 if providers else 204
    @marshal_with(providers_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('url', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. url=ssh://git@gitlab.elettra.eu:/cs/ds/)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        try:
            provider = Providers(url = args['url'])
            db.session.add(provider)
            db.session.commit()
            return provider, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class ProviderHandler(Resource):
    @marshal_with(providers_fields)
    def put(self, providerid):
        provider = Providers.query.filter(Providers.id == providerid).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('url', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. url=ssh://git@gitlab.elettra.eu:/cs/ds/)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        provider.url = args['url']
        db.session.commit()
        return provider, 201
    def delete(self, providerid):
        provider = Providers.query.filter(Providers.id == providerid).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(provider)
        db.session.commit()
        return {}, 204

class RepositoryTypeItem(fields.Raw):
    def format(self, value):
        if value == RepositoryType.cplusplus:
            return 'cplusplus'
        elif value == RepositoryType.python:
            return 'python'
        elif value == RepositoryType.shellscript:
            return 'shellscript'
        elif value == RepositoryType.library:
            return 'library'
        else: # value == RepositoryType.configuration
            return 'configuration'

repositories_fields = { 'id': fields.Integer(), 'name': fields.String(),
        'provider': fields.String(attribute='provider.url'),
        'distribution': fields.String(attribute='platform.distribution.name'),
        'version': fields.String(attribute='platform.distribution.version'),
        'architecture': fields.String(attribute='platform.architecture.name'),
        'type': RepositoryTypeItem(), 'destination': fields.String(),
        'enabled': fields.Boolean() }
class RepositoriesHandler(Resource):
    @marshal_with(repositories_fields)
    def get(self):
        repositories = Repositories.query.all()
        return repositories, 200 if repositories else 204
    @marshal_with(repositories_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=cs/ds/fake)')
        parser.add_argument('provider', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. provider=ssh://git@gitlab.elettra.eu:/cs/ds/)')
        parser.add_argument('distribution', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. architecture=ppc7400)')
        parser.add_argument('type', required=True, trim=True, nullable=False,
                choices=['cplusplus', 'python', 'shellscript', 'configuration', 'library'], type=non_empty_string,
                help='{error_msg} (e.g. type=cplusplus)')
        parser.add_argument('destination', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. destination=/bin/)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        plat = Platforms.query.with_parent(distro).with_parent(arch) \
                .first_or_404(description="Platform doesn't exist")
        prov = Providers.query.filter(Providers.url == args['provider']) \
                .first_or_404(description="Provider doesn't exist")
        try:
            repo = Repositories(name = args['name'], provider = prov, platform = plat,
                    type = RepositoryType[args['type']].value, destination=args['destination'])
            db.session.add(repo)
            db.session.commit()
            return repo, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class RepositoryHandler(Resource):
    @marshal_with(repositories_fields)
    def put(self, repositoryid):
        repo = Repositories.query.filter(Repositories.id == repositoryid).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default=repo.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=cs/ds/fake)')
        parser.add_argument('provider', default=repo.provider.url, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. provider=ssh://git@gitlab.elettra.eu:/cs/ds/)')
        parser.add_argument('distribution', default=repo.platform.distribution.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. distribution=Ubuntu)')
        parser.add_argument('version', default=repo.platform.distribution.version, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. version=18.04)')
        parser.add_argument('architecture', default=repo.platform.architecture.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. architecture=ppc)')
        parser.add_argument('type', default=RepositoryType(repo.type).name, trim=True, nullable=False,
                choices=['cplusplus', 'python', 'shellscript', 'configuration', 'library'], type=non_empty_string,
                help='{error_msg} (e.g. type=cplusplus)')
        parser.add_argument('destination', default=repo.destination, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. destination=/bin/)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        distro = Distributions.query.filter(Distributions.name == args['distribution'],
                Distributions.version == args['version']) \
                        .first_or_404(description="Distribution doesn't exist")
        arch = Architectures.query.filter(Architectures.name == args['architecture']) \
                .first_or_404(description="Architecture doesn't exist")
        plat = Platforms.query.with_parent(distro).with_parent(arch) \
                .first_or_404(description="Platform doesn't exist")
        prov = Providers.query.filter(Providers.url == args['provider']) \
                .first_or_404(description="Provider doesn't exist")
        repo.name = args['name']
        repo.provider = prov
        repo.platform = plat
        repo.type = RepositoryType[args['type']].value
        repo.destination = args['destination']
        db.session.commit()
        return repo, 201
    def delete(self, repositoryid):
        repo = Repositories.query.filter(Repositories.id == repositoryid).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(repo)
        db.session.commit()
        return {}, 204

facilities_fields = { 'name': fields.String() }
class FacilitiesHandler(Resource):
    @marshal_with(facilities_fields)
    def get(self):
        facilities = Facilities.query.all()
        return facilities, 200 if facilities else 204
    @marshal_with(facilities_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=fermi)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        try:
            fac = Facilities(name = args['name'])
            db.session.add(fac)
            db.session.commit()
            return fac, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description='Integrity error')

class FacilityHandler(Resource):
    @marshal_with(facilities_fields)
    def put(self, facilityname):
        fac = Facilities.query.filter(Facilities.name == facilityname).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default.fac.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=fermi)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        fac.name = args['name']
        db.session.commit()
        return fac, 201
    def delete(self, facilityname):
        fac = Facilities.query.filter(Facilities.name == facilityname).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(fac)
        db.session.commit()
        return {}, 204

hosts_fields = { 'name': fields.String(),
        'server': fields.String(attribute='server.name'),
        'facility': fields.String(attribute='facility.name') }
class HostsHandler(Resource):
    @marshal_with(hosts_fields)
    def get(self, facilityname):
        fac = Facilities.query.filter(Facilities.name == facilityname).first_or_404()
        hosts = Hosts.query.with_parent(fac).all()
        return hosts, 200 if hosts else 204
    @marshal_with(hosts_fields)
    def post(self, facilityname):
        parser = reqparse.RequestParser()
        parser.add_argument('name', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=ec-sl-slpsr-01)')
        parser.add_argument('server', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. server=srv-net-srf)')
        parser.add_argument('prefix', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. prefix=/runtime/)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        fac = Facilities.query.filter(Facilities.name == facilityname) \
                .first_or_404(description="Facility doesn't exist")
        srv = Servers.query.filter(Servers.name == args['server'], 
                Servers.prefix == args['prefix']) \
                .first_or_404(description="Server doesn't exist")
        try:
            host = Hosts(name = args['name'], server = srv, facility = fac)
            db.session.add(host)
            db.session.commit()
            return host, 201
        except IntegrityError:
            db.session.rollback()
        raise UnprocessableEntity(description="Integrity error")

class HostHandler(Resource):
    @marshal_with(hosts_fields)
    def put(self, facilityname, hostname):
        host = Hosts.query.filter(Hosts.name == hostname).first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('name', default=host.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. name=ec-sl-slpsr-01)')
        parser.add_argument('server', default=host.server.name, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. server=srv-net-srf)')
        parser.add_argument('prefix', default=host.server.prefix, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. prefix=/runtime/)')
        parser.add_argument('facility', default=host.facility.name, trim=True,
                nullable=False, type=non_empty_string, help='{error_msg} (e.g. fermi)')
        args = parser.parse_args(strict=True)
        authenticate(AuthenticationType.ADMIN, request)
        fac = Facilities.query.filter(Facilities.name == args['facility']) \
                .first_or_404(description="Facility doesn't exist")
        srv = Servers.query.filter(Servers.name == args['server'],
                Servers.prefix == args['prefix']) \
                .first_or_404(description="Server doesn't exist")
        host.name = args['name']
        host.server = srv
        host.facility = fac
        db.session.commit()
        return host, 201
    def delete(self, facilityname, hostname):
        host = Hosts.query.filter(Hosts.name == hostname).first_or_404()
        authenticate(AuthenticationType.ADMIN, request)
        db.session.delete(host)
        db.session.commit()
        return {}, 204


#files_fields = { 'filename': fields.String() }
#class FilesHandler(Resource):
#    @marshal_with(files_fields)
#    def get(self, facilityname, hostname):
#        host = Hosts.query.join('facility').\
#                filter(Facilities.name == facilityname,
#                        Hosts.name == hostname).\
#                first_or_404()
#        LatestInstallations = db.session.query(Installations)\
#                .with_entities(Repositories.id, Installations.host_id,\
#                    func.max(Installations.id).label('installation_id'))\
#                .select_from(Installations)\
#                .join(Builds).join(Repositories)\
#                .group_by(Repositories.id, Installations.host_id)\
#                .subquery()
#        retval = []
#        for artifact in Builds.query.join('installations').join('artifacts').\
#                join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
#                with_entities(Artifacts).filter(Installations.host == host).all():
#                    retval.append({ 'filename' : artifact.filename })
#        return retval, 200 if retval else 204
#
#file_fields = { 'filename': fields.String(), 'hash': fields.String() }
#class FileHandler(Resource):
#    @marshal_with(file_fields)
#    def get(self, facilityname, hostname, filename):
#        host = Hosts.query.join('facility').\
#                filter(Facilities.name == facilityname,
#                        Hosts.name == hostname).\
#                first_or_404()
#        LatestInstallations = db.session.query(Installations)\
#                .with_entities(Repositories.id, Installations.host_id,\
#                    func.max(Installations.id).label('installation_id'))\
#                .select_from(Installations)\
#                .join(Builds).join(Repositories)\
#                .group_by(Repositories.id, Installations.host_id)\
#                .subquery()
#        artifact = Builds.query.\
#                join('installations').\
#                join('artifacts').\
#                join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
#                with_entities(Artifacts).\
#                filter(Artifacts.filename == filename, Installations.host == host).\
#                first_or_404()
#        return { 'filename': artifact.filename, 'hash': artifact.hash }

mode_parser = reqparse.RequestParser()
mode_parser.add_argument('mode', type=str, default='status', required=False,
        choices=['status', 'diff', 'history'], location='args')

cs_installations_fields = { 'facility': fields.String(),
        'host': fields.String(), 'repository': fields.String(),
        'tag': fields.String(), 'date': fields.DateTime(),
        'author': fields.String() }
class CSInstallationsHandler(Resource):
    @marshal_with(cs_installations_fields)
    def get(self):
        args = mode_parser.parse_args(strict=True)
        LatestInstallations = db.session.query(Installations)\
                .with_entities(Repositories.id, Installations.host_id,\
                    func.max(Installations.id).label('installation_id'))\
                .select_from(Installations)\
                .join(Builds).join(Repositories)\
                .group_by(Repositories.id, Installations.host_id)\
                .subquery()
        if args['mode'] == 'status':
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True).\
                    joinedload('facility', innerjoin=True)).\
                    join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
                    order_by(Installations.date.desc()).all()
        elif args['mode'] == 'diff':
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True).\
                    joinedload('facility', innerjoin=True)).\
                    join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
                    filter(Installations.type != int(InstallationType.GLOBAL)).\
                    order_by(Installations.date.desc()).all()
        else: # history
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True).\
                    joinedload('facility', innerjoin=True)).\
                order_by(Installations.date.desc()).all()
        retval = []
        for installation in installations:
            retval.append({ 'facility': installation.host.facility.name, 
                'host': installation.host.name,
                'repository': installation.build.repository.name,
                'tag': installation.build.tag, 'date': installation.date,
                'author': installation.user.name })
        return retval, 200 if len(retval) else 204
    @marshal_with(cs_installations_fields)
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('repository', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. repository=fake)')
        parser.add_argument('tag', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. tag=0.0.4)')
        args = parser.parse_args(strict=True)
        username = authenticate(AuthenticationType.USER, request)
        destinations = {}
        for repository in Repositories.query.\
                filter(Repositories.name == args['repository']).\
                filter(Repositories.enabled == 1).\
                all():
            for server in repository.platform.servers:
                for host in server.hosts:
                    try:
                        destinations[host.server].add(host)
                    except KeyError:
                        destinations[host.server] = {host}
        return install(username, args['repository'], args['tag'],
                destinations, InstallationType.GLOBAL)

facility_installations_fields = { 'host': fields.String(),
        'repository': fields.String(), 'tag': fields.String(),
        'date': fields.DateTime(), 'author': fields.String() }
class FacilityInstallationsHandler(Resource):
    @marshal_with(facility_installations_fields)
    def get(self, facilityname):
        facility = Facilities.query.\
                filter(Facilities.name == facilityname).\
                first_or_404()
        args = mode_parser.parse_args(strict=True)
        LatestInstallations = db.session.query(Installations)\
                .with_entities(Repositories.id, Installations.host_id,\
                    func.max(Installations.id).label('installation_id'))\
                .select_from(Installations)\
                .join(Builds).join(Repositories)\
                .group_by(Repositories.id, Installations.host_id)\
                .subquery()
        if args['mode'] == 'status':
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True)).\
                    join('host').\
                    join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
                    filter(Hosts.facility == facility).\
                    order_by(Installations.date.desc()).all()
        elif args['mode'] == 'diff':
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True)).\
                    join('host').\
                    join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
                    filter(Hosts.facility == facility,
                            Installations.type == int(InstallationType.HOST)).\
                    order_by(Installations.date.desc()).all()
        else: # history
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True)).\
                    join('host').\
                    filter(Hosts.facility == facility).\
                    order_by(Installations.date.desc()).all()
        retval = []
        for installation in installations:
            retval.append({ 'host': installation.host.name,
                'repository': installation.build.repository.name,
                'tag': installation.build.tag, 'date': installation.date,
                'author': installation.user.name })
        return retval, 200 if len(retval) else 204
    @marshal_with(facility_installations_fields)
    def post(self, facilityname):
        facility = Facilities.query.filter(Facilities.name == facilityname) \
                .first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('repository', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. repository=fake)')
        parser.add_argument('tag', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. tag=0.0.4)')
        args = parser.parse_args(strict=True)
        username = authenticate(AuthenticationType.USER, request)
        destinations = {}
        for repository in Repositories.query.\
                filter(Repositories.name == args['repository']).\
                filter(Repositories.enabled == 1).\
                all():
                    for server in repository.platform.servers:
                        for host in server.hosts:
                            if host.facility_id != facility.id:
                                continue
                            try:
                                destinations[host.server].add(host)
                            except KeyError:
                                destinations[host.server] = {host}
        return install(username, args['repository'], args['tag'], 
                destinations, InstallationType.FACILITY)

host_installations_fields = { 'repository': fields.String(),
        'tag': fields.String(), 'date': fields.DateTime(),
        'author': fields.String() }
class HostInstallationsHandler(Resource):
    @marshal_with(host_installations_fields)
    def get(self, facilityname, hostname):
        host = Hosts.query.join('facility').\
                filter(Facilities.name == facilityname,
                        Hosts.name == hostname).\
                first_or_404()
        args = mode_parser.parse_args(strict=True)
        LatestInstallations = db.session.query(Installations)\
                .with_entities(Repositories.id, Installations.host_id,\
                    func.max(Installations.id).label('installation_id'))\
                .select_from(Installations)\
                .join(Builds).join(Repositories)\
                .group_by(Repositories.id, Installations.host_id)\
                .subquery()
        if args['mode'] == 'status':
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True)).\
                    join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
                    filter(Installations.host == host).\
                    order_by(Installations.date.desc()).all()
        elif args['mode'] == 'diff':
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True)).\
                    join(LatestInstallations, Installations.id == LatestInstallations.c.installation_id).\
                    filter(Installations.host == host,
                            Installations.type == int(InstallationType.HOST)).\
                    order_by(Installations.date.desc()).all()
        else: # history
            installations = Installations.query.options(
                    joinedload('user', innerjoin=True),\
                    joinedload('build', innerjoin=True).\
                    joinedload('repository', innerjoin=True),
                    joinedload('host', innerjoin=True)).\
                    filter(Installations.host == host).\
                    order_by(Installations.date.desc()).all()
        retval = []
        for installation in installations:
            retval.append({ 'repository': installation.build.repository.name,
                'tag': installation.build.tag, 'date': installation.date,
                'author': installation.user.name })
        return retval, 200 if len(retval) else 204
    @marshal_with(host_installations_fields)
    def post(self, facilityname, hostname):
        host = Hosts.query.options(joinedload('facility', innerjoin=True)) \
                .filter(Facilities.name == facilityname, Hosts.name == hostname) \
                .first_or_404()
        parser = reqparse.RequestParser()
        parser.add_argument('repository', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. repository=fake)')
        parser.add_argument('tag', required=True, trim=True, nullable=False,
                type=non_empty_string, help='{error_msg} (e.g. tag=0.0.4)')
        args = parser.parse_args(strict=True)
        username = authenticate(AuthenticationType.USER, request)
        destinations = {}
        destinations[host.server] = {host}
        return install(username, args['repository'], args['tag'], 
                destinations, InstallationType.HOST)
        return {}

def retrieveAnnotatedTags(gitrepo):
    atags = set()
    for atag in gitrepo.tags:
        if atag.tag != None:
            atags.add(atag)
    return atags

def updateRepo(repo):
    gitrepo = None
    atagsBefore = set()
    repoPath = app.config['GIT_TREES_DIR'] + repo.name
    if os.path.isdir(repoPath):
        gitrepo = git.Repo(repoPath)
        gitrepo.git.clean("-fdx")
        gitrepo.git.reset('--hard')
        gitrepo.git.checkout("master")
        atagsBefore = retrieveAnnotatedTags(gitrepo)
        gitrepo.remotes.origin.fetch()
        gitrepo.submodule_update(recursive=True, init=False, force_reset=True)
    else:
        gitrepo = git.Repo.clone_from(repo.provider.url + repo.name, to_path=repoPath)
        gitrepo.submodule_update(recursive=True, init=True, force_reset=False)
    atagsAfter = set()
    atagsAfter = retrieveAnnotatedTags(gitrepo)
    return gitrepo, atagsAfter - atagsBefore


if __name__ == '__main__':
    # Configure Flask
    app.config['SQLALCHEMY_DATABASE_URI'] = args.db
    app.config['MAIL_SERVER'] = args.smtpserver
    app.config['MAIL_DOMAIN'] = args.smtpdomain
    app.config['MAIL_DEFAULT_SENDER'] = args.smtpsender + "@" + args.smtpdomain
    app.config['FILES_STORE_DIR'] = args.store
    app.config['GIT_TREES_DIR'] = args.repo
    app.config['LDAP_URL'] = args.ldap
    app.config['BUNDLE_ERRORS'] = True
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    if args.port != "443":
        app.debug = True
        app.config['SQLALCHEMY_ECHO'] = True

    # Configure SQLAclhemy
    db.app = app
    db.init_app(app)

    # Create all DB tables if necessary
    db.create_all()

    # Creates REST endpoints
    v2.add_resource(CSHandler, '/cs', '/cs/')
    v2.add_resource(UsersHandler, '/cs/users', '/cs/users/')
    v2.add_resource(UserHandler, '/cs/users/<string:username>',
            '/cs/users/<string:username>/')
    v2.add_resource(ArchitecturesHandler, '/cs/architectures', '/cs/architectures/')
    v2.add_resource(ArchitectureHandler, '/cs/architectures/<string:archname>',
            '/cs/architectures/<string:archname>/')
    v2.add_resource(DistributionsHandler, '/cs/distributions', '/cs/distributions/')
    v2.add_resource(DistributionHandler, '/cs/distributions/<string:distroid>',
            '/cs/distributions/<string:distroid>/')
    v2.add_resource(PlatformsHandler, '/cs/platforms', '/cs/platforms/')
    v2.add_resource(PlatformHandler, '/cs/platforms/<int:platid>',
            '/cs/platforms/<int:platid>/')
    v2.add_resource(BuildersHandler,'/cs/builders', '/cs/builders/')
    v2.add_resource(BuilderHandler, '/cs/builders/<string:buildername>',
            '/cs/builders/<string:buildername>/')
    v2.add_resource(ServersHandler, '/cs/servers', '/cs/servers/')
    v2.add_resource(ServerHandler, '/cs/servers/<string:servername>',
            '/cs/servers/<string:servername>/')
    v2.add_resource(ProvidersHandler, '/cs/providers', '/cs/providers/')
    v2.add_resource(ProviderHandler, '/cs/servers/<int:providerid>',
            '/cs/providers/<int:providerid>/')
    v2.add_resource(RepositoriesHandler, '/cs/repositories', '/cs/repositories/')
    v2.add_resource(RepositoryHandler, '/cs/repositories/<int:repositoryid>',
            '/cs/repositories/<int:repositoryid>/')
    v2.add_resource(FacilitiesHandler, '/cs/facilities', '/cs/facilities/')
    v2.add_resource(FacilityHandler, '/cs/facilities/<string:facilityname>',
            '/cs/facilities/<string:facilityname>/')
    v2.add_resource(HostsHandler, '/cs/facilities/<string:facilityname>/hosts',
            '/cs/facilities/<string:facilityname>/hosts/')
    v2.add_resource(HostHandler, 
            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>',
            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>/')
#    v2.add_resource(FilesHandler, 
#            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>/files',
#            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>/files/')
#    v2.add_resource(FileHandler,
#            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>/files/<string:filename>',
#            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>/files/<string:filename>/')

    v2.add_resource(CSInstallationsHandler, '/cs/installations',
            '/cs/installations/', '/cs/facilities/installations',
            '/cs/facilities/installations/')
    v2.add_resource(FacilityInstallationsHandler, 
            '/cs/facilities/<string:facilityname>/installations', 
            '/cs/facilities/<string:facilityname>/installations/', 
            '/cs/facilities/<string:facilityname>/hosts/installations',
            '/cs/facilities/<string:facilityname>/hosts/installations/')
    v2.add_resource(HostInstallationsHandler,
            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>/installations',
            '/cs/facilities/<string:facilityname>/hosts/<string:hostname>/installations/')

    if args.port != "443":
        app.run(host='0.0.0.0', port=args.port, threaded=True,
                use_reloader=False, use_debugger=False)
    else:
        app.run(host='0.0.0.0', port=args.port, threaded=True,
                ssl_context=('/etc/ssl/certs/inau_elettra_eu.pem',
                    '/etc/ssl/private/inau_elettra_eu.key'),
                use_reloader=False, use_debugger=False)
