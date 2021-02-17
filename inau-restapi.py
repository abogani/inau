#!/usr/bin/env python3

#from flask import Flask, make_response, request, session
#from flask_classful import FlaskView, route
#from marshmallow import Schema, pre_load, post_load, post_dump, fields, ValidationError
#from webargs.flaskparser import use_kwargs
#from sqlalchemy import create_engine
#from sqlalchemy.orm import scoped_session, sessionmaker, exc
#from werkzeug.exceptions import HTTPException, Unauthorized, Forbidden, InternalServerError, MethodNotAllowed, BadRequest, UnprocessableEntity, NotFound, PreconditionFailed, UnsupportedMediaType
#from enum import Enum, IntEnum
#from datetime import timedelta
#import argparse
#import uuid
#import json
#import ldap
#import base64
#from lib import text, db, schema

#app = Flask(__name__)
#app.secret_key = str(uuid.uuid4())
#dbsession_factory = sessionmaker()

#class AuthenticationType(Enum):
#    USER = 0,
#    ADMIN = 1

#def authenticate(authtype, dbsession, request):
#    session.permanent = True
#    if "username" not in session:
#        if request.headers.get('Authorization') == None:
#            raise Unauthorized()
#        split = request.headers.get('Authorization').strip().split(' ')
#        username, password = base64.b64decode(split[1]).decode().split(':', 1)
#        user = dbsession.query(db.Users).filter(db.Users.name == username).first()
#        if user is None:
#           raise Forbidden()
#        if authtype == AuthenticationType.ADMIN and user.admin is False:
#            raise Forbidden()
#        try:
#            auth = ldap.initialize(args.ldap, bytes_mode=False)
#            auth.simple_bind_s("uid=" + username +",ou=people,dc=elettra,dc=eu", password)
#            auth.unbind_s()
#        except Exception as e:
#            raise Forbidden()
#        session["username"] = username
#    return session["username"]

#@app.teardown_request
#def dbsession_remove(exc):
#    DBSession.remove()

#def output_json(data, code, headers=None):
#    content_type = 'application/json'
#    dumped = json.dumps(data)
#    if headers:
#        headers.update({'Content-Type': content_type})
#    else:
#        headers = {'Content-Type': content_type}
#    response = make_response(dumped, code, headers)
#    return response

#def output_text(data, code, headers=None):
#    content_type = 'text/plain'
#    dumped = text.dumps(data)
#    if headers:
#        headers.update({'Content-Type': content_type})
#    else:
#        headers = {'Content-Type': content_type}
#    response = make_response(dumped, code, headers)
#    return response

#default_representations = {
#        'flask-classful/default': output_json,
#        'application/json': output_json,
#        'text/plain': output_text
#        }
#
#class UsersView3(FlaskView):
#    route_base = "users"
#    representations = default_representations
#    def index(self):
#        dbsession = DBSession()
#        users = dbsession.query(db.Users) \
#                .order_by(db.Users.id).all()
#        return {'users': schema.users_schema.dump(users)}, 200 if users else 204
##    def get(self, id: int):
##        dbsession = DBSession()
##        user = dbsession.query(db.Users).filter(db.Users.id==id).one_or_none()
##        return user_schema.dump(user), 200 if user else 204
##    def delete(self, id: int):
##        dbsession = DBSession()
##        try:
##            authenticate(AuthenticationType.ADMIN, dbsession, request)
##            user = dbsession.query(db.Users).filter(db.Users.id==id).one()
##            dbsession.delete(user)
##            dbsession.commit()
##        except exc.NoResultFound as n:
##            return {"error": str(n)}, 404
##        except HTTPException as h:
##            return {"error": h.description}, h.code
##        except Exception as e:
##            dbsession.rollback()
##            return {"error": str(e)}, 500
##        return {}, 204
##    @use_kwargs(user_schema.fields, location="json_or_form")
##    def put(self, id: int, **kwargs):
### TODO
##        pass
##    @use_kwargs(user_schema.fields, location="json_or_form")
##    def patch(self, id: int, **kwargs):
##        dbsession = DBSession()
##        try:
##            authenticate(AuthenticationType.ADMIN, dbsession, request)
##            newuser = user_schema.load(kwargs)
##            user = dbsession.query(db.Users).filter(db.Users.id==id).one()
##            if newuser.name is not None: user.name = newuser.name
##            if newuser.admin is not None: user.admin = newuser.admin
##            if newuser.notify is not None: user.notify = newuser.notify
##            dbsession.commit()
##        except exc.NoResultFound as n:
##            return {"error": str(n)}, 404
##        except HTTPException as h:
##            return {"error": h.description}, h.code
##        except Exception as e:
##            dbsession.rollback()
##            return {"error": str(e)}, 500
##        return user_schema.dump(user), 200
##    @use_kwargs(user_schema.fields, location="json_or_form")
##    def post(self, **kwargs):
##        dbsession = DBSession()
##        try:
##            authenticate(AuthenticationType.ADMIN, dbsession, request)
##            user = user_schema.load(kwargs)
##            dbsession.add(user)
##            dbsession.commit()
##        except ValidationError as v:
##            return {"error": str(v)}, 500
##        except HTTPException as h:
##            return {"error": h.description}, h.code
##        except Exception as e:
##            dbsession.rollback()
##            return {"error": str(e)}, 500
##        return user_schema.dump(user), 201
#
#class PlatformsView3(FlaskView):
#    route_base = "platforms"
#    representations = default_representations
#    def index(self):
#        dbsession = DBSession()
#        platforms = dbsession.query(db.Platforms) \
#                .order_by(db.Platforms.id).all()
#        return {'platforms': schema.platforms_schema.dump(platforms) }, \
#                200 if platforms else 204
#
#class FacilitiesView3(FlaskView):
#    route_base = "facilities"
#    representations = default_representations
#    def index(self):
#        dbsession = DBSession()
#        facilities = dbsession.query(db.Facilities) \
#                .order_by(db.Facilities.id).all()
#        return {'facilities': schema.facilities_schema.dump(facilities)}, \
#                200 if facilities else 204
#
#class ServersView3(FlaskView):
#    route_base = "servers"
#    representations = default_representations
#    def index(self):
#        dbsession = DBSession()
#        servers = dbsession.query(db.Servers) \
#                .order_by(db.Servers.name).all()
#        return {'servers': schema.servers_schema.dump(servers)}, \
#                200 if servers else 204
#
##class HostsView3(FlaskView):
##    route_base = "hosts"
##    representations = default_representations
##    def index(self, facility):
##        print(facility)
##        dbsession = DBSession()
##        hosts = dbsession.query(db.Hosts) \
##                .order_by(db.Hosts.id).all()
##        return {'hosts': schema.hosts_schema.dump(hosts)}, \
##                200 if hosts else 204
#
#UsersView3.register(app, route_prefix='/v3/cs/')
#PlatformsView3.register(app, route_prefix='/v3/cs/')
#ServersView3.register(app, route_prefix='/v3/cs/')
#FacilitiesView3.register(app, route_prefix='/v3/cs/')
##HostsView3.register(app, route_prefix='/v3/cs/<string>/')
#
#if __name__ == '__main__':
#    parser_args = argparse.ArgumentParser()
#    parser_args.add_argument("--db", type=str, help='Database URI to connect to', required=True)
#    parser_args.add_argument('--bind', type=str, default='localhost', help='IP Address or hostname to bind to')
#    parser_args.add_argument('--port', type=int, default=8080, help='Port to listen to')
#    parser_args.add_argument("--store", default="/scratch/build/files-store/")
#    parser_args.add_argument("--ldap", default="ldaps://abook.elettra.eu:636")
#    args = parser_args.parse_args()
#
#    engine = create_engine(args.db, pool_pre_ping=True, echo=True)
#    dbsession_factory.configure(bind=engine)
#    DBSession = scoped_session(dbsession_factory)
#
#    app.run(host=args.bind, port=args.port, threaded=True,
#            ssl_context=('/etc/ssl/certs/inau_elettra_eu.crt',
#                '/etc/ssl/private/inau_elettra_eu.key'))
