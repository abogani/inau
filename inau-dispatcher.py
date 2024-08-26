#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
from http import HTTPStatus
import ssl
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, exc
from multiprocessing import Process, Queue
from enum import Enum, IntEnum
import os
import signal
import json
import sys
import logging
import logging.handlers
import argparse
import datetime
#import time
import subprocess
import paramiko
import hashlib
import shutil
#import requests
#import urllib.parse
from smtplib import SMTP
from email.mime.text import MIMEText
#from distutils.version import StrictVersion

from lib import db

Session = sessionmaker()

allbuilders = {}
users = {}

# TODO Build a repository against ALL platforms and avoid emails entirely

def __sendEmail(to_addrs, subject, body):
    if to_addrs:
        with SMTP(args.smtpserver + "." + args.smtpdomain, port=25) as smtpClient:
            sender = args.smtpsender + "@" + args.smtpdomain
            msg = MIMEText(body)
            msg['Subject'] = "INAU. " + subject
            msg['From'] = sender
            msg['To'] = ', '.join(to_addrs)
            d = smtpClient.sendmail(from_addr=sender, to_addrs=list(to_addrs), msg=msg.as_string())
            print("Email sent to ", list(to_addrs), d)

def sendEmail(recipients, subject, body):
    notifiable = set()
    for user in users:
        if (user.notify==True):
            notifiable.add(user.name + "@" + args.smtpdomain)
    to_addrs = set(recipients).intersection(notifiable)
    __sendEmail(to_addrs, subject, body)

def sendEmailAdmins(subject, body):
    to_addrs = set()
    for user in users:
        if (user.admin==True):
            to_addrs.add(user.name + "@" + args.smtpdomain)
    __sendEmail(to_addrs, subject, body)

# TODO Move follow classes to a separate file
class Die:
    pass

class Update:
    def __init__(self, repository_name, repository_url, build_tag):
        self.repository_name = repository_name
        self.repository_url = repository_url
        self.build_tag = build_tag

class Build(Update):
    def __init__(self, repository_name, repository_url, build_tag):
        Update.__init__(self, repository_name, repository_url, build_tag)
        self.status = ''
        self.output = ''

class Store(Build):
    def __init__(self, repository_name, repository_url, build_tag, repository_id, repository_type, emails):
        Build.__init__(self, repository_name, repository_url, build_tag)
        self.repository_id = repository_id
        self.repository_type = repository_type
        self.emails = emails

class Builder:
    def __init__(self, name, platform_id, environment):
        self.name = name
        self.platform_id = platform_id
        self.platdir = args.repo + '/' + str(platform_id)
        if environment is None:
            self.environment = ""
        else:
            self.environment = "source " + environment + "; "
        self.queue = Queue()
        self.process = Process(target=self.handler)
        self.process.start()

    def update(self, job):
        logger.info("[" + self.name + "] Checkouting " + job.build_tag + " from " + job.repository_url + "...")
        builddir = self.platdir + "/" + job.repository_name
        # Wait a second before start to update git repository to avoid:
        # Command '['git -C /scratch/build/repositories//8/cs/etc/browser/booster reset --hard refs/tags/1.0.34']' returned non-zero exit status 128.
        # time.sleep(1)
        if not os.path.isdir(self.platdir):
            os.mkdir(self.platdir)

        if not os.path.isdir(self.platdir + "/cs/ds/makefiles"):
            subprocess.run(["git clone --recurse-submodule https://gitlab.elettra.eu/cs/ds/makefiles.git " + self.platdir + "/cs/ds/makefiles"], shell=True, check=True)
        else:
            subprocess.run(["git -C " + self.platdir + "/cs/ds/makefiles remote update"], shell=True, check=True)
            subprocess.run(["git -C " + self.platdir + "/cs/ds/makefiles reset --hard origin/master"], shell=True, check=True)

        if not os.path.isdir(builddir):
            subprocess.run(["git clone --recurse-submodule " + job.repository_url + " " + builddir], shell=True, check=True)
        subprocess.run(["git -C " + builddir + " remote update"], shell=True, check=True)
        subprocess.run(["git -C " + builddir + " submodule update --init --force --recursive"], shell=True, check=True)
        subprocess.run(["git -C " + builddir + " fetch --tags"], shell=True, check=True)
#       subprocess.run(["git -C " + builddir + " pull"], shell=True, check=True)
#       subprocess.run(['git branch --no-color --contains ' + job.build_tag + ' master | grep \<master\>', 
        subprocess.run(["git -C " + builddir + " reset --hard " + job.build_tag + " --"], shell=True, check=True)

    def build(self, job):
        logging.info("[" + self.name + "] Building " + job.build_tag + " from " + job.repository_url + "...")
        builddir = self.platdir + "/" + job.repository_name
        with paramiko.SSHClient() as sshClient:
            sshClient.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            sshClient.connect(hostname=self.name, port=22, username="inau",
                    key_filename="/home/inau/.ssh/id_rsa.pub")
            if job.repository_type != db.RepositoryType.library:
                _, raw, _ = sshClient.exec_command("(" + self.environment + "source /etc/profile; cd " + builddir
                        + " && (test -f *.pro && qmake && cuuimake --plain-text-output);"
                        + " make -j`getconf _NPROCESSORS_ONLN`) 2>&1")
            else:
                _, raw, _ = sshClient.exec_command("(" + self.environment + "source /etc/profile; cd " + builddir
                        + " && (test -f *.pro && qmake && cuuimake --plain-text-output);"
                        + " make -j`getconf _NPROCESSORS_ONLN` && rm -fr .install && PREFIX=.install make install)  2>&1")
            job.status = raw.channel.recv_exit_status()
            job.output = raw.read().decode('latin-1') # utf-8 is rejected by Mysql despite it is properly configured

    def store(self, job):
        logging.info("[" + self.name + "] Storing " + job.build_tag + " from " + job.repository_url + "...")
            
        build = db.Builds(repository_id=job.repository_id, platform_id=self.platform_id, tag=os.path.basename(job.build_tag), 
                status=job.status, output=job.output)
        self.session.add(build)
        self.session.commit()

        builddir = self.platdir + "/" + job.repository_name
        outcome = job.repository_name + " " + os.path.basename(job.build_tag) 
        if job.status != 0:
            outcome += ": built failed on " + self.name
        else:
            outcome += ": built successfully on " + self.name
            if job.repository_type == db.RepositoryType.cplusplus or job.repository_type == db.RepositoryType.python \
                    or job.repository_type == db.RepositoryType.shellscript:
                basedir = builddir + "/bin/"
            elif job.repository_type == db.RepositoryType.configuration:
                basedir = builddir + "/etc/"
            elif job.repository_type == db.RepositoryType.library:
                basedir = builddir + "/.install/"
            else:
                raiseException('Invalid type')

            artifacts = []
            for rdir_abs, _, fnames in os.walk(basedir):
                for fname in fnames:
                    fname_abs = os.path.join(rdir_abs, fname)
                    rdir_rel = os.path.relpath(rdir_abs, basedir)
                    fname_rel = os.path.join(rdir_rel, fname)
#                    print("rdir_abs ", rdir_abs, "fname", fname, "fname_abs", fname_abs,
#                            "rdir_rel", rdir_rel, "fname_rel", fname_rel)
                    if not os.path.islink(fname_abs):
                        with open(fname_abs,"rb") as fd:
                                bytes = fd.read()
                                hashFile = hashlib.sha256(bytes).hexdigest();
                                if not os.path.isfile(args.store + hashFile):
                                    shutil.copyfile(fname_abs, args.store + hashFile, follow_symlinks=False)
                                artifacts.append(db.Artifacts(build_id=build.id, hash=hashFile, filename=fname_rel))
                    else:
                        artifacts.append(db.Artifacts(build_id=build.id, filename=fname_rel,
                            symlink_target=os.path.join(rdir_rel, os.readlink(fname_abs))))
            self.session.add_all(artifacts)
            self.session.commit()
        sendEmail(job.emails, outcome, job.output)

    def handler(self):
        logger.info("[" + self.name + "] Starting process for builder " +  self.name + "...")
            
        engine.dispose()
        self.session = Session()
        while True:
            try:
                job = self.queue.get()
                if isinstance(job, Die):
                    logger.info("[" + self.name + "] Stopping process for builder " + self.name + "...")
                    break

                if isinstance(job, Update):
                    self.update(job)

                if isinstance(job, Build):
                    self.build(job)

                if isinstance(job, Store):
                    self.store(job)

            except subprocess.CalledProcessError as c:
                sendEmailAdmins("Subprocess failed", str(c))
                logger.error("Subprocess failed: ", str(c))
                self.session.rollback()
            except Exception as e:
                sendEmailAdmins("Generic error", str(e))
                logger.error("Generic error: ", str(e))
                self.session.rollback()
            except KeyboardInterrupt as k:
                self.session.rollback()
                break
            finally:
                self.session.close()

def signalHandler(signalNumber, frame):
    reconcile()

def reconcile():
    logger.info('Reconciling...')

    session = Session()
    try:
        global allbuilders
        global users

        users = session.query(db.Users).all()

        newbuilders = {}
        oldbuilders = allbuilders
        for b in session.query(db.Builders).all():
            try:
                newbuilders[b.platform_id].append(Builder(b.name, b.platform_id, b.environment))
            except KeyError:
                newbuilders[b.platform_id] = [Builder(b.name, b.platform_id, b.environment)]
        allbuilders = newbuilders

        for oldbuilder in oldbuilders.values():
            for b in oldbuilder:
                b.queue.put(Die())
                b.process.join()
# TODO build missing tags
        # Build missing tags
#        for repo in session.query(db.Repositories).join(db.Providers). \
#                filter(db.Repositories.name == "cs/ds/fake"). \
#                with_entities(db.Repositories.id, db.Repositories.name, 
#                        db.Repositories.type, db.Repositories.platform_id, db.Providers.url).all():
#            req = requests.get('https://gitlab.elettra.eu/api/v4/projects/' 
#                    + urllib.parse.quote(repo.name, safe='') + '/repository/tags')
#            data = req.json()
#            if req.status_code != 200:
#                logger.error('Error looking for repository ' + repo.name);
#            else:
#                # Retrieve committed tags
#                ctags = []
#                for tag in data:
#                    if tag['target'] != tag['commit']['id']:
#                        ctags.append(tag['name'])
#                ctags.sort(key=StrictVersion)
#                print(ctags)
#
#                # Retrieve built tags
#                btags = []
#                for build in session.query(db.Builds).filter(db.Builds.repository_id==repo.id).all():
#                    btags.append(build.tag)
#                btags.sort(key=StrictVersion)
#                print(btags)
#
#                # Calculate missing tags
#                mtags = list(set(ctags).difference(set(btags)))
#                mtags.sort(key=StrictVersion)
#                print(mtags)
#                        
#                if mtags:
#                    i = ctags.index(mtags[0])
#                    if i:
#                        # Re-build the previous built version
#                        idx = allbuilders[repo.platform_id].index(min(allbuilders[repo.platform_id], 
#                            key=lambda x:x.queue.qsize()))
#                        allbuilders[repo.platform_id][idx].queue.put(Job(type=JobType.build, 
#                            repository_name = repo.name, repository_url = repo.url+ "/" + repo.name + ".git",
#                            repository_type = repo.type, 
#                            build_tag='refs/tags/' + ctags[i-1]))
#
#                    # Build missing tags
#                    for mtag in mtags:
#                        emails = []
#                        for tag in data:
#                            if tag['name'] == mtag:
#                                emails = [tag['commit']['author_email']]
#                                break
#                        build = db.Builds(repository_id=repo.id, platform_id=repo.platform_id, tag=mtag)
#                        session.add(build)
#                        session.commit()
#
#                        idx = allbuilders[repo.platform_id].index(min(allbuilders[repo.platform_id], 
#                            key=lambda x:x.queue.qsize()))
#                        allbuilders[repo.platform_id][idx].queue.put(Job(type=JobType.build, 
#                            repository_name = repo.name, repository_url = repo.url+ "/" + repo.name + ".git",
#                            repository_type = repo.type, build_tag='refs/tags/' + mtag, 
#                            build_id = build.id, emails = emails))
    except Exception as e:
        sendEmailAdmins("Reconcilation failed", str(e))
        logger.error("Reconciliation failed: ", str(e))
        session.rollback()
    finally:
        session.close()

class Server(BaseHTTPRequestHandler):
    def do_POST(self):
        engine.dispose()
        session = Session()
        try:
            content_length = int(self.headers['Content-Length']) 
            post_data = self.rfile.read(content_length)

            if self.headers['Content-Type'] != 'application/json':
                self.send_response(HTTPStatus.UNSUPPORTED_MEDIA_TYPE.value)
                self.end_headers()
                return

            post_json = json.loads(post_data.decode('utf-8'))
            logger.debug(post_json)

            # Tag deletion
            if post_json['after'] == '0000000000000000000000000000000000000000':
                self.send_response(HTTPStatus.OK.value)
                self.end_headers()
                return

            # Check if the tag is lightweight
            if post_json['after'] == post_json['commits'][0]['id']:
                self.send_response(HTTPStatus.OK.value)
                self.end_headers()
                return

            for r in session.query(db.Repositories).filter(db.Repositories.name==post_json['project']['path_with_namespace']).all():
                if self.headers['X-Gitlab-Event'] == 'Tag Push Hook' and post_json['event_name'] == 'tag_push' and r.enabled:
                    job = Store(repository_name = r.name, repository_url = post_json['project']['ssh_url'], build_tag=post_json['ref'],
                            repository_id = r.id, repository_type = r.type, emails=[post_json['commits'][0]['author']['email'], 
                                post_json['user_username'] + '@elettra.eu', post_json['user_email']])
                else:
                    continue

                # Assign the job to the builder with shortest queue length
                idx = allbuilders[r.platform_id].index(min(allbuilders[r.platform_id], 
                    key=lambda x:x.queue.qsize()))
                logger.info("Assign building of " + r.name + " to " + allbuilders[r.platform_id][idx].name)
                allbuilders[r.platform_id][idx].queue.put(job)

            self.send_response(HTTPStatus.OK.value)
            self.end_headers()

        except Exception as e:
            sendEmailAdmins("Receive new tag failed", str(e))
            logger.error("Receive new tag failed: ", str(e))
            session.rollback()
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR.value)
            self.end_headers()
        finally:
            session.close()

# TODO Migrate to multi-thread HTTP server?
def run(address, port, server_class=HTTPServer, handler_class=Server):
    logger.info('Starting...')
    server_address = (address, port)
    httpd = server_class(server_address, handler_class)
#    httpd.socket = ssl.wrap_socket (httpd.socket, keyfile="/etc/ssl/private/inau_elettra_eu.key",
#            certfile="/etc/ssl/certs/inau_elettra_eu.crt", server_side=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logger.info('Stopping...')

if __name__ == '__main__':
    # TODO Migrate to configuration file to avoid hard-coded defaults
    # Retrieve arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, help='Database URI to connect to', required=True) # TODO Enable password-less access to database
    parser.add_argument('--bind', type=str, default='localhost', help='IP Address or hostname to bind to')
    parser.add_argument('--port', type=int, default=443, help='Port to listen to')
    parser.add_argument("--store", type=str, default='/scratch/build/files-store/', help='Directory where store produced binaries')
    parser.add_argument("--repo", type=str, default='/scratch/build/repositories/', help='Directory where checkout git repositories')
    parser.add_argument("--smtpserver", type=str, default="smtp", help='Hostname of the SMTP server')
    parser.add_argument("--smtpsender", type=str, default="noreply", help='Email sender')
    parser.add_argument("--smtpdomain", type=str, default="elettra.eu", help='Email domain')
    args = parser.parse_args()

    print("Start inau-dispatcher using", args.db, "on interface", args.bind, "and port", 
            args.port, "file store directory", args.store, "repositories clone directory", args.repo, 
            "SMTP server", args.smtpserver, "SMTP sender", args.smtpsender, "SMTP domain", args.smtpdomain)

    if os.getpgrp() == os.tcgetpgrp(sys.stdout.fileno()):
        # Executed in foreground so redirect log to terminal and enable SQL echoing (Development)
        logging.basicConfig(level=logging.INFO)
        engine = create_engine(args.db, pool_pre_ping=True, echo=True)
    else:
        # Executed in background so redirect log to syslog and disable SQL echoing (Production)
        syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
        logging.basicConfig(level=logging.INFO, handlers=[syslog_handler])
        engine = create_engine(args.db, pool_pre_ping=True, echo=False)
    logger = logging.getLogger('inau-dispatcher')

    Session.configure(bind=engine)

    reconcile()
    signal.signal(signal.SIGHUP, signalHandler)

    if args.bind:
            run(args.bind,args.port)
