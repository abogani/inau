#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
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
#repositories = []

def __sendEmail(to_addrs, subject, body):
    if to_addrs:
        with SMTP(args.smtpserver + "." + args.smtpdomain, port=25) as smtpClient:
            sender = args.smtpsender + "@" + args.smtpdomain
            msg = MIMEText(body)
            msg['Subject'] = "INAU. " + subject
            msg['From'] = sender
            msg['To'] = ', '.join(to_addrs)
            smtpClient.sendmail(from_addr=sender, to_addrs=list(to_addrs), msg=msg.as_string())

def sendEmail(session, recipients, subject, body):
    users = set()
    for user in session.query(db.Users).filter(db.Users.notify==True).all():
        users.add(user.name + "@" + args.smtpdomain)
    to_addrs = set(recipients).intersection(users)
    __sendEmail(to_addrs, subject, body)

def sendEmailAdmins(session, subject, body):
    to_addrs = set()
    for admin in session.query(db.Users).filter(db.Users.admin==True).all():
        to_addrs.add(admin.name + "@" + args.smtpdomain)
    __sendEmail(to_addrs, subject, body)

class JobType(IntEnum):
    kill = 0,
    build = 1,
    update = 2

class Job:
    def __init__(self, type, repository_name=None, repository_url=None, repository_type=None,
            platform_id=None, build_tag=None, build_id=None, emails=None):
        self.type = type
        self.repository_name = repository_name
        self.repository_url = repository_url
        self.repository_type = repository_type
        self.build_tag = build_tag
        self.platform_id = platform_id
        self.build_id = build_id
        self.emails = emails

class Builder:
    def __init__(self, name):
        self.name = name
        self.queue = Queue()
        self.process = Process(target=self.build, name=name)
        self.process.start()
    def build(self):
        print("Parte buidler di " + self.name) # FIXME Debug
        while True:
            try:
                print("buidler di " + self.name + " in attesa...") # FIXME Debug
                job = self.queue.get()

                if job.type == JobType.kill:
                    print("Si ferma buodler di " + self.name) # FIXME Debug
                    break

                print("buidler di " + self.name + " in azione... su: ") # FIXME Debug
                print(job.type) # FIXME Debug
                print(job.repository_name) # FIXME Debug
                print(job.repository_url) # FIXME Debug
                print(job.repository_type) # FIXME Debug
                print(job.build_tag) # FIXME Debug
                print(job.platform_id) # FIXME Debug
                print(job.build_id) # FIXME Debug
                print(job.emails) # FIXME Debug

                engine.dispose()
                session = Session()

                try:
                    platdir = args.repo + '/' + str(job.platform_id)
                    builddir = platdir + "/" + job.repository_name
                    if not os.path.isdir(platdir):
                        os.mkdir(platdir)
                    if os.path.isdir(builddir):
                        subprocess.run(["git -C " + builddir + " remote update"], shell=True, check=True)
                        subprocess.run(["git -C " + builddir + " submodule update --remote --force --recursive"], shell=True, check=True)
                    else:
                        ret = subprocess.run(["git clone --recurse-submodule " + job.repository_url + " " + builddir], shell=True, check=True)
                    subprocess.run(["git -C " + builddir + " reset --hard " + job.build_tag], shell=True, check=True)
                
                    if job.type == JobType.update:
                        continue

                    with paramiko.SSHClient() as sshClient:
                        sshClient.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        sshClient.connect(hostname=self.name, port=22, username="inau",
                                key_filename="/home/inau/.ssh/id_rsa.pub")
                        _, raw, _ = sshClient.exec_command("(source /etc/profile; cd " + builddir 
                                            + " && (test -f *.pro && qmake && cuuimake --plain-text-output);"
                                            + " make -j`getconf _NPROCESSORS_ONLN`) 2>&1")
                        status = raw.channel.recv_exit_status()
                        output = raw.read().decode('latin-1') # utf-8 is rejected by Mysql despite the right character set is configured

                    if job.build_id:
                        build = session.query(db.Builds).filter(db.Builds.id==job.build_id).one()
                        build.date = datetime.datetime.now()
                        build.status = status
                        build.output = output
                        session.commit()

                    outcome = job.repository_name + " " + os.path.basename(job.build_tag) 
                    if status != 0:
                        outcome += ": built failed on " + self.name
                    else:
                        outcome += ": built successfully on " + self.name
                        if job.build_id:
                            if job.repository_type == db.RepositoryType.cplusplus or job.repository_type == db.RepositoryType.python \
                                    or job.repository_type == db.RepositoryType.shellscript:
                                basedir = builddir + "/bin/"
                            elif job.repository_type == db.RepositoryType.configuration:
                                basedir = builddir + "/etc/"
                            else:
                                raiseException('Invalid type')

                            artifacts = []
                            for r, d, f in os.walk(basedir):
                                dir = ""
                                if r != basedir:
                                    dir = os.path.basename(r) + "/"
                                for file in f:
                                    hashFile = ""
                                    with open(basedir + dir + file,"rb") as fd:
                                        bytes = fd.read()
                                        hashFile = hashlib.sha256(bytes).hexdigest();
                                        if not os.path.isfile(args.store + hashFile):
                                            shutil.copyfile(basedir + dir + file, args.store + hashFile, follow_symlinks=False)
                                        artifacts.append(db.Artifacts(build_id=job.build_id, hash=hashFile, filename=dir+file))
                            session.add_all(artifacts)
                            session.commit()

                    sendEmail(session, job.emails, outcome, output)

                except subprocess.CalledProcessError as c:
                    print("C 1:", c)    # TODO
                except Exception as e:
                    session.rollback()
                    print("E 1:", e, type(e))    # TODO
                finally:
                    session.close()

            # TODO Come funzione in background?????
            except KeyboardInterrupt as k:
                break
            except Exception as e:
                print("E 2: ", e)    # TODO


def signalHandler(signalNumber, frame):
    reconcile()

def reconcile():
    logger.info('Reconciling...')

    session = Session()

    try:
#        global allbuilders, repositories
        global allbuilders

        newbuilders = {}
        oldbuilders = {}
        for b in session.query(db.Builders).all():
            try:
                newbuilders[b.platform_id].append(Builder(b.name))
            except KeyError:
                newbuilders[b.platform_id] = [Builder(b.name)]
        oldbuilders = allbuilders
        allbuilders = newbuilders

        for oldbuilder in oldbuilders.values():
            for b in oldbuilder:
                b.queue.put(Job(type=JobType.kill))
                b.process.join()

#        newrepositories = []
#        for repository in session.query(db.Repositories2).all():
#            newrepositories.append(repository)
#        repositories = newrepositories
#
#        for repo in session.query(db.Repositories2).join(db.Providers). \
#                with_entities(db.Repositories2.id, db.Repositories2.name, db.Repositories2.type, db.Providers.url).all():
#            req = requests.get('https://gitlab.elettra.eu/api/v4/projects/' 
#                    + urllib.parse.quote(repo.name, safe='') + '/repository/tags')
#            data = req.json()
#            if req.status_code == 200:
#                # Retrieve commited tags
#                ctags = []
#                for tag in data:
#                    if tag['target'] != tag['commit']['id']:
#                        ctags.append(tag['name'])
#                ctags.sort(key=StrictVersion)
#
#                for platform_id, builders in allbuilders.items():
#                    builds = session.query(db.Builds).filter(db.Builds.repository_id==repo.id, 
#                            db.Builds.platform_id==platform_id).all()
#                    # Retrieve builded tags
#                    btags = []
#                    for build in builds:
#                        btags.append(build.tag)
#                    btags.sort(key=StrictVersion)
#
#                    mtags = list(set(ctags).difference(set(btags)))
#                    mtags.sort(key=StrictVersion)
#                    
#                    if mtags:
#                        i = ctags.index(mtags[0])
#                        if i:
#                            # Re-build the previous built version
#                            idx = builders.index(min(builders, key=lambda x:x.queue.qsize()))
#                            builders[idx].queue.put(Job(type=JobType.build, repository_name = repo.name,
#                                repository_url = repo.url + ":" + repo.name, repository_type = repo.type, 
#                                platform_id = platform_id, build_tag = ctags[i-1]))
#
#                        # Build missing tags
#                        emails = []
#                        for mtag in mtags:
#                            idx = builders.index(min(builders, key=lambda x:x.queue.qsize()))
#                            emails.clear()
#                            for tag in data:
#                                if tag['name'] == mtag:
#                                    emails = [tag['commit']['author_email']]
#                                    break
#                            build = db.Builds(repository_id=repo.id, platform_id=platform_id, tag=mtag)
#                            session.add(build)
#                            session.commit()
#   
#                            builders[idx].queue.put(Job(type=JobType.build, repository_name = repo.name,
#                                repository_url = repo.url + ":" + repo.name, repository_type = repo.type, 
#                                platform_id = platform_id, build_tag = mtag, build_id = build.id, emails=emails))
#
    except Exception as e:
        session.rollback()
        print("E 3: ", e)     # TODO
    finally:
        session.close()

class Server(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length']) 
        post_data = self.rfile.read(content_length)

        if self.headers['Content-Type'] != 'application/json':
            self.send_response(415)
            self.end_headers()
            return

        post_json = json.loads(post_data.decode('utf-8'))
        print(post_json) # FIXME DEBUG

        # Tag deletion
        if post_json['after'] == '0000000000000000000000000000000000000000':
            self.send_response(415)
            self.end_headers()
            return

        # Check if the tag is lightweight
        if post_json['after'] == post_json['commits'][0]['id']:
            self.send_response(400)
            self.end_headers()
            return

        builds = []
        rn = ''
        rt = ''

        session = Session()
        for r in session.query(db.Repositories).filter(db.Repositories.name==post_json['project']['path_with_namespace']).all():
            rn = r.name
            rt = r.type
            if r.name == "cs/ds/makefiles" and self.headers['X-Gitlab-Event'] == 'Push Hook' and post_json['event_name'] == 'push':
                jt = JobType.update 
            elif self.headers['X-Gitlab-Event'] == 'Tag Push Hook' and post_json['event_name'] == 'tag_push':
                jt = JobType.build
            else:
                self.send_response(400)
                self.end_headers()
                session.close()
                return

            builds.append(db.Builds(repository_id=r.id, platform_id=r.platform_id, tag=os.path.basename(post_json['ref'])))
               
        if not builds:
            self.send_response(404)
            self.end_headers()
            session.close()
            return

        if jt == JobType.build:
            try:
                session.add_all(builds)
                session.commit()
            except:
                session.rollback()
                session.close()
                self.send_response(500)
                self.end_headers()
                return
       
        for build in builds:
            print('Assign the job to the builder with shortest queue length...')
            idx = allbuilders[build.platform_id].index(min(allbuilders[build.platform_id], 
                key=lambda x:x.queue.qsize()))
            allbuilders[build.platform_id][idx].queue.put(Job(type=jt, repository_name = rn, 
                repository_url = post_json['project']['http_url'], repository_type = rt, 
                platform_id = build.platform_id, build_tag=post_json['ref'], build_id=build.id, 
                emails=[post_json['commits'][0]['author']['email'], post_json['user_email']]))

        self.send_response(200)
        self.end_headers()

        session.close()

def run(address, port, server_class=HTTPServer, handler_class=Server):
    logger.info('Starting...')
    server_address = (address, port)
    httpd = server_class(server_address, handler_class)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logger.info('Stopping...')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, help='Database URI to connect to', required=True)
    parser.add_argument('--bind', type=str, default='localhost', help='IP Address or hostname to bind to')
    parser.add_argument('--port', type=int, default=443, help='Port to listen to')
    parser.add_argument("--store", type=str, default='/scratch/build/files-store/')
    parser.add_argument("--repo", type=str, default='/scratch/build/repositories/')
    parser.add_argument("--smtpserver", type=str, default="smtp")
    parser.add_argument("--smtpsender", type=str, default="noreply")
    parser.add_argument("--smtpdomain", type=str, default="elettra.eu")
    args = parser.parse_args()

    if os.getpgrp() == os.tcgetpgrp(sys.stdout.fileno()):
        # Executed in foreground (Development)
        logging.basicConfig(level=logging.INFO)
        engine = create_engine(args.db, pool_pre_ping=True, echo=True)
    else:
        # Executed in background (Production)
        syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
        logging.basicConfig(level=logging.INFO, handlers=[syslog_handler])
        engine = create_engine(args.db, pool_pre_ping=True, echo=False)

    logger = logging.getLogger('inauDispatcher')
    
    Session.configure(bind=engine)

    reconcile()

    signal.signal(signal.SIGUSR1, signalHandler)

    if args.bind:
            run(args.bind,args.port)

    # FIXME It is necessary?
    for platform_id, builders in allbuilders.items():
        for builder in builders:
            builder.process.join()
