#from marshmallow import Schema, fields
#
#class UserSchema(Schema):
#    id = fields.Integer(dump_only=True)
#    name = fields.Str()
#    admin = fields.Bool()
#    notify = fields.Bool()
#    class Meta:
#        ordered = True
##    @post_load
##    def make_user(self, data, **kwargs):
##        return db.Users(**data)
#
#user_schema = UserSchema(only=('name', 'admin', 'notify'))
#users_schema = UserSchema(many=True)
#
#class DistributionSchema(Schema):
#    id = fields.Integer(dump_only=True)
#    name = fields.Str()
#    version = fields.Str()
#    formatted = fields.Method('format_output', dump_only=True)
#    def format_output(self, distribution):
#        return "{} {}".format(distribution.name, distribution.version)
#
#class ArchitectureSchema(Schema):
#    id = fields.Integer(dump_only=True)
#    name = fields.Str()
#
#class PlatformSchema(Schema):
#    id = fields.Integer(dump_only=True)
##    distribution_id = fields.Integer(dump_only=True)
#    distribution = fields.Pluck('DistributionSchema', 'formatted')
##    architecture_id = fields.Integer(dump_only=True)
#    architecture = fields.Pluck('ArchitectureSchema', 'name')
#    formatted = fields.Method('format_output', dump_only=True)
#    def format_output(self, platform):
#        return "{} {} {}".format(platform.distribution.name, 
#                platform.distribution.version, platform.architecture.name)
#    class Meta:
#        ordered = True
##    @post_load
##    def make_platform(self, data, **kwargs):
##        return db.Platforms(**data)
#
#platform_schema = PlatformSchema(only=('distribution', 'architecture'))
#platforms_schema = PlatformSchema(many=True, only=('id', 'distribution', 'architecture'))
#
#class FacilitySchema(Schema):
#    id = fields.Integer(dump_only=True)
#    name = fields.Str()
#    class Meta:
#        ordered = True
##    @post_load
##    def make_facility(self, data, **kwargs):
##        return db.Facilities(**data)
#
#facility_schema = FacilitySchema(only=('name',))
#facilities_schema = FacilitySchema(many=True)
#
#class ServerSchema(Schema):
#    id = fields.Integer(dump_only=True)
#    name = fields.Str()
#    prefix = fields.Str()
##    platform_id = fields.Integer(dump_only=True)
#    platform = fields.Pluck('PlatformSchema', 'formatted')
#    formatted = fields.Method('format_output', dump_only=True)
#    def format_output(self, server):
#        return "{} {} {} {} {}".format(server.name, server.prefix, 
#                server.platform.distribution.name,
#                server.platform.distribution.version, 
#                server.platform.architecture.name)
#    class Meta:
#        ordered = True
##    @post_load
##    def make_server(self, data, **kwargs):
##        return db.Servers(**data)
#
#server_schema = ServerSchema(only=('name', 'prefix','platform'))
#servers_schema = ServerSchema(many=True, only=('id','name','prefix','platform'))
#
##class HostSchema(Schema):
##    id = fields.Integer(dump_only=True)
##    facility_id = fields.Integer(dump_only=True)
##    server_id = fields.Integer(dump_only=True)
##    name = fields.Str()
##    facility = fields.Pluck('FacilitySchema', 'name')
##    server = fields.Pluck('ServerSchema', 'formatted')
##    class Meta:
##        ordered = True
##    @post_load
##    def make_server(self, data, **kwargs):
##        return db.Servers(**data)
#
##host_schema = HostSchema(only=('name','facility','server'))
##hosts_schema = HostSchema(many=True)
