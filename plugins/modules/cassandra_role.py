#!/usr/bin/python

# Copyright: (c) 2019, Rhys Campbell <rhys.james.campbell@googlemail.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import absolute_import, division, print_function


DOCUMENTATION = r'''
---
module: cassandra_role
short_description: Manage roles on your Cassandra cluster.
description: Manage roles on your Cassandra Cluster.
author: Rhys Campbell (@rhysmeister)
options:
  login_user:
    description: The Cassandra user to login with.
    type: str
  login_password:
    description: The Cassandra password to login with.
    type: str
  ssl:
    description: Uses SSL encryption if basic SSL encryption is enabled on Cassandra cluster (without client/server verification)
    type: bool
    default: False
  ssl_cert_reqs:
    description: SSL verification mode.
    type: str
    choices:
      - 'CERT_NONE'
      - 'CERT_OPTIONAL'
      - 'CERT_REQUIRED'
    default: 'CERT_NONE'
  ssl_ca_certs:
    description:
        The SSL CA chain or certificate location to confirm supplied certificate validity
        (required when  ssl_cert_reqs is set to CERT_OPTIONAL or CERT_REQUIRED)
    type: str
    default: ''
  login_host:
    description: The Cassandra hostname.
    type: list
    elements: str
  login_port:
    description: The Cassandra port.
    type: int
    default: 9042
  name:
    description: The name of the role to create or manage.
    type: str
    required: true
  state:
    description: The desired state of the role.
    type: str
    choices:
      - "present"
      - "absent"
    required: true
  super_user:
    description:
      - If the user is a super user or not.
    type: bool
    default: false
  login:
    description:
      - True allows the role to log in.
      - Use true to create login accounts for internal authentication, PasswordAuthenticator or DSE Unified Authenticator.
    type: bool
    default: true
  password:
    description:
      - The password for the role.
    type: str
  update_password:
    description:
      - Passwords are not handled by default. With this set to true, passwords are always overridden.
      - The task will always be considered changed if this is set to true.
    type: bool
    default: false
  options:
    description:
      - Reserved for use with authentication plug-ins. Refer to the authenticator documentation for details.
    type: dict
  data_centres:
    description:
      - Only relevant if a network_authorizer has been configured.
      - Specify data centres as keys of this dict.
      - Can specify a key as 'all' although this implicity assumed by Cassandra if not supplied.
    type: dict
    aliases:
      - data_centers
  keyspace_permissions:
    description:
      - Grant privileges on keyspace objects.
      - Specify keyspaces as keys of this dict.
      - Permissions supplied as a list to the keyspace keys.
      - Valid permissions at keyspace level are as follows; ALL PERMISSIONS, CREATE, ALTER, AUTHORIZE, DROP, MODIFY, SELECT
      - A special key 'all_keyspaces' can be supplied to assign permissions to all keyspaces.
    type: dict
  roles:
    description:
      - One or more roles to grant to this user or role.
      - When set to None, the default, no action is perform on roles.
      - Set to an empty list to revoke all roles.
    type: list
    elements: str
  debug:
    description:
      - Additional debug output.
    type: bool
    default: false
  consistency_level:
    description:
      - Consistency level to perform cassandra queries with.
      - Not all consistency levels are supported by read or write connections.\
        When a level is not supported then LOCAL_ONE, the default is used.
      - Consult the README.md on GitHub for further details.
    type: str
    default: "LOCAL_ONE"
    choices:
        - ANY
        - ONE
        - TWO
        - THREE
        - QUORUM
        - ALL
        - LOCAL_QUORUM
        - EACH_QUORUM
        - SERIAL
        - LOCAL_SERIAL
        - LOCAL_ONE
'''

EXAMPLES = r'''
- name: Create a role
  community.cassandra.cassandra_role:
    name: app_user
    password: 'secretZHB78'
    state: present
    login: yes

- name: Remove a role
  community.cassandra.cassandra_role:
    name: app_user
    state: absent

- name: Create a super user
  community.cassandra.cassandra_role:
    name: admin
    password: 'BigSecretUser2019'
    state: present
    login: yes
    super_user: yes

- name: Create a user with access only to certain data centres
  community.cassandra.cassandra_role:
    name: rhys
    password: 'secret'
    state: present
    login: yes
    data_centres:
      london:
      zurich:

- name: Create a user with specific permissions for specific keyspaces
  community.cassandra.cassandra_role:
    name: rhys
    password: 'secret'
    update_password: true
    state: present
    login: yes
    permissions:
      mykeyspace:
        - "ALL PERMISSIONS" # Same as GRANT ALL PERMISSIONS ON mykeyspace TO rhys;
      mydummy:
        - "SELECT"
        - "EXECUTE" # Same as GRANT SELECT, EXECUTE ON mydummy TO rhys;
      all_keyspaces:
        - "SELECT" # Same as GRANT SELECT ON ALL KEYSPACES TO rhys;
'''


RETURN = '''
changed:
  description: Whether the module has changed the role.
  returned: on success
  type: bool
cql:
  description: The cql used to create or alter the role.
  returned: changed
  type: str
  sample: "ALTER ROLE admin /
   WITH SUPERUSER = true /
   AND LOGIN = true /
   AND PASSWORD = 'XXXXXXXX'"
role:
  description: The role operated on.
  returned: on success
  type: str
msg:
  description: Exceptions encountered during module execution.
  returned: on error
  type: str
'''

__metaclass__ = type
import os.path

try:
    from cassandra.cluster import Cluster
    from cassandra.cluster import EXEC_PROFILE_DEFAULT
    from cassandra.cluster import ExecutionProfile
    from cassandra.auth import PlainTextAuthProvider
    from cassandra import AuthenticationFailed
    from cassandra.query import dict_factory
    from cassandra import InvalidRequest
    from cassandra import ConsistencyLevel
    HAS_CASSANDRA_DRIVER = True
except Exception:
    HAS_CASSANDRA_DRIVER = False

from ansible.module_utils.basic import AnsibleModule

try:
    from ssl import SSLContext, PROTOCOL_TLS
    import ssl as ssl_lib
    HAS_SSL_LIBRARY = True
except Exception:
    HAS_SSL_LIBRARY = False

# =========================================
# Cassandra module specific support methods
# =========================================


# Does the role exist on the cluster?
def role_exists(session, role):
    cql = "SELECT role FROM system_auth.roles WHERE role = '{0}'".format(role)
    roles = session.execute(cql)
    s = False
    if len(list(roles)) > 0:
        s = True
    return s


def get_role_properties(session, role):
    cql = "SELECT role, can_login, is_superuser, member_of, salted_hash FROM system_auth.roles WHERE role = '{0}'".format(role)
    dict_factory_profile = session.execution_profile_clone_update(EXEC_PROFILE_DEFAULT, row_factory=dict_factory)
    role_properties = session.execute(cql, execution_profile=dict_factory_profile)
    return role_properties[0]


def is_role_changed(role_properties, super_user, login, password,
                    options, data_centres, update_password):
    '''
    Determines whether a role has changed and therefore needs /
    to be changed with an ALTER ROLE statement.
    role_properties - Dictionary created from the system_auth.roles keyspace?
    super_user - User provided boolean value.
    login - User provided boolean value.
    password - User provided string value. Not currently dealt with.
    options - User provided value. Not currently dealt with.
    data_centres - User provided dictionary value. Not currently dealt with.
    '''
    changed = False
    if role_properties['is_superuser'] != super_user:
        changed = True
    elif role_properties['can_login'] != login:
        changed = True
    elif update_password is True:
        changed = True
    return changed


def create_alter_role(module, role, super_user, login, password,
                      options, data_centres, alter_role):
    if alter_role is False:
        cql = "CREATE ROLE '{0}' ".format(role)
    else:
        cql = "ALTER ROLE '{0}' ".format(role)
    cql += "WITH SUPERUSER = {0} ".format(super_user)
    cql += "AND LOGIN = {0} ".format(login)
    if password is not None:
        cql += "AND PASSWORD = '{0}' ".format(password)
    if options is not None:
        cql += "AND OPTIONS = {0}".format(str(options))
    if data_centres is not None:
        for dc in data_centres:
            if str(dc.upper()) == "ALL" and len(data_centres) == 1:
                cql += " AND ACCESS TO ALL DATACENTERS"
                break
            else:
                if len(data_centres) == 1:
                    cql += " AND ACCESS TO DATACENTERS {{'{0}'}}".format(str(dc))
                    break
                else:
                    cql += " AND ACCESS TO DATACENTERS {{'{0}'}}".format("','".join(data_centres))
                    break
    return cql


def create_role(role):
    ''' Used for creating roles that are assigned to other users
    '''
    cql = "CREATE ROLE '{0}'".format(role)
    return cql


def grant_role(role, grantee):
    ''' Assign roles to other roles
    '''
    cql = "GRANT '{0}' TO '{1}'".format(role,
                                        grantee)
    return cql


def revoke_role(role, grantee):
    ''' Revoke a role
    '''
    cql = "REVOKE '{0}' FROM '{1}'".format(role,
                                           grantee)
    return cql


def drop_role(role):
    cql = "DROP ROLE '{0}'".format(role)
    return cql


def validate_keyspace_permissions(keyspace_permissions):
    '''
    All keyspace permissions must exist in the perms list
    '''
    perms = [
        "ALL PERMISSIONS",
        "CREATE",
        "ALTER",
        "AUTHORIZE",
        "DROP",
        "MODIFY",
        "SELECT"
    ]

    invalid_dict = {}

    for k in keyspace_permissions.keys():
        for v in keyspace_permissions[k]:
            if v not in perms:
                return False
    return True


def grant_permission(permission, role, keyspace):
    if keyspace == "all_keyspaces":
        cql = "GRANT {0} ON ALL KEYSPACES TO '{1}'".format(permission,
                                                           role)
    else:
        cql = "GRANT {0} ON KEYSPACE {1} TO '{2}'".format(permission,
                                                          keyspace,
                                                          role)
    return cql


def revoke_permission(permission, role, keyspace):
    cql = "REVOKE {0} ON KEYSPACE {1} FROM '{2}'".format(permission,
                                                         keyspace,
                                                         role)
    return cql


def list_role_permissions(session, role):
    '''
    Returned by LIST ALL OF cassandra;

     role      | username  | resource               | permission
    -----------+-----------+------------------------+------------
     cassandra | cassandra |        <all keyspaces> |     SELECT
     cassandra | cassandra |        <all keyspaces> |     MODIFY
     cassandra | cassandra |        <keyspace rhys> |      ALTER
     cassandra | cassandra |        <keyspace rhys> |       DROP
     cassandra | cassandra |        <keyspace rhys> |     SELECT
     cassandra | cassandra |        <keyspace rhys> |     MODIFY
     cassandra | cassandra |        <keyspace rhys> |  AUTHORIZE
     cassandra | cassandra | <keyspace system_auth> |     SELECT
     cassandra | cassandra | <keyspace system_auth> |     MODIFY



     Returns a resultset object of dicts
    '''
    cql = "LIST ALL OF '{0}'".format(role)
    try:
        dict_factory_profile = session.execution_profile_clone_update(EXEC_PROFILE_DEFAULT, row_factory=dict_factory)
        role_permissions = session.execute(cql, execution_profile=dict_factory_profile)
    except InvalidRequest as excep:
        # excep_code = type(excep).__name__
        # if excep_code == 2200: # User does not exist
        role_permissions = []
    return role_permissions


def does_role_have_permission(role_permissions,
                              permission,
                              keyspace):
    '''
    Returns true if the permission is already assigned to the role.
    ALTER DROP SELECT MODIFY AUTHORIZE CREATE - The result from "ALL PERMISSIONS"
    '''
    match_count = 0  # When 6 then the permission is matched
    matched = False
    all_permissions = [
        "ALTER",
        "DROP",
        "SELECT",
        "MODIFY",
        "AUTHORIZE",
        "CREATE"
    ]
    if keyspace == "all_keyspaces":
        resource = "<all keyspaces>"
    else:
        resource = "<keyspace {0}>".format(keyspace)
    for row in role_permissions:
        if permission == "ALL PERMISSIONS":  # we need to check for CREATE ALTER DROP SELECT MODIFY AUTHORIZE
            if row['permission'].strip() in all_permissions and row['resource'].strip() == resource:
                match_count += 1
        else:  # specific permission check
            if row['permission'].strip() == permission and row['resource'].strip() == resource:
                match_count = len(all_permissions)  # specific permission match straight away
    if match_count == len(all_permissions):
        matched = True
    return matched


def build_role_grants(session,
                      role,
                      roles):
    '''
    Builds the cql for granting and revoking roles from users
    @session - Cassandra connection
    @role - The role to grant or revoke roles from
    @roles - The list of roles supplied via the module

    Returns - A dictionary structure containing GRANT
    and remove cql statements for roles
    '''
    roles_dict = {
        "grant": set(),
        "revoke": set()
    }

    role_permissions = list_role_permissions(session, role)

    current_roles = set()
    for permission in role_permissions:
        if permission['role'] != role:
            current_roles.add(permission['role'])
        else:
            pass  # We don't touch other perms here
    # Revokes first, roles should be an empty list to revoke all
    if current_roles is not None and roles is not None:
        for r in current_roles:
            if r not in roles:
                cql = revoke_role(r,
                                  role)
                roles_dict['revoke'].add(cql)
    # grants
    if roles is not None:
        for r in roles:
            if r not in current_roles:
                cql = grant_role(r,
                                 role)
                roles_dict['grant'].add(cql)
    return roles_dict


def build_role_permissions(session,
                           keyspace_permissions,
                           role):
    '''
    session - Cassandra cluster session.
    keyspace_permissions - Dictionary containing new keyspace permissions
    role - The Cassandra role name

    Returns - A dictionary structure containing GRANT and remove cql statements

    {
        "grant": ["GRANT SELECT ON KEYSPACE rhys TO cassandra",
                  "GRANT ALL PERMISSIONS ON KEYSPACE rhys TO admin"],
        "revoke": ["REVOKE SELECT ON KEYSPACE rhys FROM app_user",
                   "REVOKE ALL PERMISSIONS ON ALL KEYSPACES FROM legacy_app"]
    }

    '''

    perms_dict = {
        "grant": set(),
        "revoke": set(),
        "temp": set()
    }

    # Permissions to grant
    if keyspace_permissions is not None:
        for keyspace in keyspace_permissions.keys():
            for permission in keyspace_permissions[keyspace]:
                role_permissions = list_role_permissions(session,
                                                         role)
                bool = does_role_have_permission(role_permissions,
                                                 permission,
                                                 keyspace)
                perms_dict['temp'].add("{0} {1} {2}".format(permission, keyspace, bool))

                if bool:
                    pass  # permission is already assigned
                else:
                    cql = grant_permission(permission,
                                           role,
                                           keyspace)
                    perms_dict['grant'].add(cql)
    # If the all_keyspaces key does not exist and there are "<all keyspaces>"
    # resources present we can revoke all
    # Permissions to revoke from specific keyspaces
    role_permissions = list_role_permissions(session, role)  # need to reset resultset
    for permission in role_permissions:
        if keyspace_permissions is not None:
            for keyspace in keyspace_permissions.keys():
                if permission['resource'] == "<all keyspaces>"\
                        and "all_keyspaces" not in keyspace_permissions.keys()\
                        and permission['role'] == role:
                    cql = "REVOKE ALL PERMISSIONS ON ALL KEYSPACES FROM '{0}'".format(role)
                    if cql not in perms_dict['revoke']:  # only do this the once
                        perms_dict['revoke'].add(cql)
                elif permission['resource'] == "<all keyspaces>" \
                        and "ALL PERMISSIONS" in keyspace_permissions[keyspace]\
                        and permission['role'] == role:
                    # No revokes needed
                    pass
                elif permission['resource'].startswith('<keyspace') \
                        and permission['role'] == role:
                    ks = permission['resource'].split(' ')[1].replace('>', '').strip()
                    if ks in keyspace_permissions.keys() \
                            and permission['permission'] not in keyspace_permissions[ks] \
                            and "ALL PERMISSIONS" not in keyspace_permissions[ks]:
                        cql = revoke_permission(permission['permission'],
                                                role,
                                                ks)
                        perms_dict['revoke'].add(cql)
            # This is for the case when the keyspace permission has not been provided
            if permission['resource'].startswith('<keyspace') \
                    and permission['role'] == role \
                    and permission['resource'].split(' ')[1].replace('>', '') not in keyspace_permissions.keys():
                cql = revoke_permission(permission['permission'],
                                        role,
                                        ks)
                perms_dict['revoke'].add(cql)
        else:
            current_roles = set()
            if permission['resource'].startswith('<keyspace') \
                    and permission['role'] == role:  # We don't touch other permissions
                ks = permission['resource'].split(' ')[1].replace('>', '')
                cql = revoke_permission(permission['permission'],
                                        role,
                                        ks)
                perms_dict['revoke'].add(cql)
    return perms_dict


def process_role_permissions(session,
                             keyspace_permissions,
                             role):
    cql_dict = build_role_permissions(session,
                                      keyspace_permissions,
                                      role)
    return cql_dict


def get_read_and_write_sessions(login_host,
                                login_port,
                                auth_provider,
                                ssl_context,
                                consistency_level):
    profile = ExecutionProfile(
        consistency_level=ConsistencyLevel.name_to_value[consistency_level])
    if consistency_level in ["ANY", "EACH_QUORUM"]:  # Not supported for reads
        cluster_r = Cluster(login_host,
                            port=login_port,
                            auth_provider=auth_provider,
                            ssl_context=ssl_context)  # Will be LOCAL_ONE
    else:
        cluster_r = Cluster(login_host,
                            port=login_port,
                            auth_provider=auth_provider,
                            ssl_context=ssl_context,
                            execution_profiles={EXEC_PROFILE_DEFAULT: profile})
    if consistency_level in ["SERIAL", "LOCAL_SERIAL"]:  # Not supported for writes
        cluster_w = Cluster(login_host,
                            port=login_port,
                            auth_provider=auth_provider,
                            ssl_context=ssl_context)  # Will be LOCAL_ONE
    else:
        cluster_w = Cluster(login_host,
                            port=login_port,
                            auth_provider=auth_provider,
                            ssl_context=ssl_context,
                            execution_profiles={EXEC_PROFILE_DEFAULT: profile})
    return (cluster_r, cluster_w)  # Return a tuple of sessions for C* (read, write)

############################################


def main():
    module = AnsibleModule(
        argument_spec=dict(
            login_user=dict(type='str'),
            login_password=dict(type='str', no_log=True),
            ssl=dict(type='bool', default=False),
            ssl_cert_reqs=dict(type='str',
                               required=False,
                               default='CERT_NONE',
                               choices=['CERT_NONE',
                                        'CERT_OPTIONAL',
                                        'CERT_REQUIRED']),
            ssl_ca_certs=dict(type='str', default=''),
            login_host=dict(type='list', elements='str'),
            login_port=dict(type='int', default=9042),
            name=dict(type='str', required=True),
            password=dict(type='str', required=False, no_log=True),
            state=dict(type='str', required=True, choices=['present', 'absent']),
            super_user=dict(type='bool', default=False),
            login=dict(type='bool', default=True),
            options=dict(type='dict'),
            data_centres=dict(type='dict', aliases=['data_centers']),
            keyspace_permissions=dict(type='dict', no_log=False),
            roles=dict(type='list', elements='str'),
            update_password=dict(type='bool', default=False),
            debug=dict(type='bool', default=False),
            consistency_level=dict(type='str',
                                   required=False,
                                   default="LOCAL_ONE",
                                   choices=ConsistencyLevel.name_to_value.keys())),
        supports_check_mode=True
    )

    if HAS_CASSANDRA_DRIVER is False:
        msg = ("This module requires the cassandra-driver python"
               " driver. You can probably install it with pip"
               " install cassandra-driver.")
        module.fail_json(msg=msg)

    login_user = module.params['login_user']
    login_password = module.params['login_password']
    ssl = module.params['ssl']
    login_host = module.params['login_host']
    login_port = module.params['login_port']
    name = module.params['name']
    role = name
    password = module.params['password']
    update_password = module.params['update_password']
    state = module.params['state']
    super_user = module.params['super_user']
    login = module.params['login']
    options = module.params['options']
    data_centres = module.params['data_centres']
    keyspace_permissions = module.params['keyspace_permissions']
    roles = module.params['roles']
    debug = module.params['debug']
    consistency_level = module.params['consistency_level']

    if HAS_SSL_LIBRARY is False and ssl is True:
        msg = ("This module requires the SSL python"
               " library. You can probably install it with pip"
               " install ssl.")
        module.fail_json(msg=msg)

    ssl_cert_reqs = module.params['ssl_cert_reqs']
    ssl_ca_certs = module.params['ssl_ca_certs']

    if ssl_cert_reqs in ('CERT_REQUIRED', 'CERT_OPTIONAL') and ssl_ca_certs == '':
        msg = ("When verify mode is set to CERT_REQUIRED or CERT_OPTIONAL "
               "ssl_ca_certs is also required to be set and not empty")
        module.fail_json(msg=msg)

    if ssl_cert_reqs in ('CERT_REQUIRED', 'CERT_OPTIONAL') and os.path.exists(ssl_ca_certs) is not True:
        msg = ("ssl_ca_certs certificate: File not found")
        module.fail_json(msg=msg)

    result = dict(
        changed=False,
        role=name,
    )

    cql = None

    # For now we won't change the role if it already exists unless the user forces it
    # Need to figure out password hashing
    # https://shiro.apache.org/configuration.html#Configuration-EncryptingPasswords

    try:
        if keyspace_permissions is not None:
            if not validate_keyspace_permissions(keyspace_permissions):
                module.fail_json(msg=("Invalid permission provided in the "
                                 "keyspace_permission parameter."))
        auth_provider = None
        if login_user is not None:
            auth_provider = PlainTextAuthProvider(
                username=login_user,
                password=login_password
            )
        ssl_context = None
        if ssl is True:
            ssl_context = SSLContext(PROTOCOL_TLS)
            ssl_context.verify_mode = getattr(ssl_lib, module.params['ssl_cert_reqs'])
            if ssl_cert_reqs in ('CERT_REQUIRED', 'CERT_OPTIONAL'):
                ssl_context.load_verify_locations(module.params['ssl_ca_certs'])

        sessions = get_read_and_write_sessions(login_host,
                                               login_port,
                                               auth_provider,
                                               ssl_context,
                                               consistency_level)

        session_r = sessions[0].connect()
        session_w = sessions[1].connect()

    except AuthenticationFailed as auth_failed:
        module.fail_json(msg="Authentication failed: {0}".format(auth_failed))
    except Exception as excep:
        module.fail_json(msg="Error connecting to cluster: {0}".format(excep))

    has_role_changed = False

    try:
        if debug:
            result['role_exists'] = role_exists(session_r, role)
        if login:  # Standard user
            if role_exists(session_r, role):
                # Has the role changed?
                role_properties = get_role_properties(session_r,
                                                      role)
                has_role_changed = is_role_changed(role_properties,
                                                   super_user,
                                                   login,
                                                   password,
                                                   options,
                                                   data_centres,
                                                   update_password)
                if debug:
                    result['has_role_changed'] = has_role_changed
                if module.check_mode:
                    if state == "present":
                        result['changed'] = has_role_changed
                    elif state == "absent":
                        result['changed'] = True
                else:
                    if state == "present":
                        # create the role
                        if has_role_changed:
                            cql = create_alter_role(module,
                                                    role,
                                                    super_user,
                                                    login,
                                                    password,
                                                    options,
                                                    data_centres,
                                                    has_role_changed)
                            session_w.execute(cql)
                            result['changed'] = True
                            result['cql'] = cql
                    elif state == "absent":
                        cql = drop_role(role)
                        session_w.execute(cql)
                        result['changed'] = True
                        result['cql'] = cql
            else:
                if module.check_mode:
                    if state == "present":
                        result['changed'] = True
                    if state == "absent":
                        result['changed'] = False
                else:
                    if state == "present":
                        cql = create_alter_role(module,
                                                role,
                                                super_user,
                                                login,
                                                password,
                                                options,
                                                data_centres,
                                                False)
                        session_w.execute(cql)
                        result['changed'] = True
                        result['cql'] = cql
                    elif state == "absent":
                        result['changed'] = False
        else:  # This is a role
            if role_exists(session_r, role):
                if module.check_mode:
                    if state == "present":
                        result['changed'] = False
                    elif state == "absent":
                        cql = drop_role(role)
                        session_w.execute(cql)
                        result['changed'] = True
                        result['cql'] = cql
                else:
                    if state == "present":
                        result['changed'] = False
                    elif state == "absent":
                        cql = drop_role(role)
                        session_w.execute(cql)
                        result['changed'] = True
                        result['cql'] = cql
            else:
                if module.check_mode:
                    if state == "present":
                        cql = create_alter_role(module,
                                                role,
                                                super_user,
                                                login,
                                                password,
                                                options,
                                                data_centres,
                                                has_role_changed)
                        session_w.execute(cql)
                        result['changed'] = True
                        result['cql'] = cql
                    elif state == "absent":
                        result['changed'] = False
                else:
                    if state == "present":
                        cql = create_role(role)
                        session_w.execute(cql)
                        result['changed'] = True
                        result['cql'] = cql
                    elif state == "absent":
                        result['changed'] = False

        if state == "present":
            cql_dict = process_role_permissions(session_r,
                                                keyspace_permissions,
                                                role)
            if len(cql_dict['grant']) > 0 or len(cql_dict['revoke']) > 0:
                for r in cql_dict['revoke']:
                    if not module.check_mode:
                        session_w.execute(r)
                for g in cql_dict['grant']:
                    if not module.check_mode:
                        session_w.execute(g)
                result['permissions'] = cql_dict
                result['changed'] = True

            # Process roles
            roles_dict = build_role_grants(session_r,
                                           role,
                                           roles)

            if len(roles_dict['grant']) > 0 or len(roles_dict['revoke']) > 0:
                result['roles'] = roles_dict
                for r in roles_dict['revoke']:
                    if not module.check_mode:
                        session_w.execute(r)
                for g in roles_dict['grant']:
                    if not module.check_mode:
                        session_w.execute(g)

                result['changed'] = True

        module.exit_json(**result)

    except Exception as excep:
        msg = str(excep)
        if cql is not None:
            msg += " | {0}".format(cql)
        if debug:
            module.fail_json(msg=msg, **result)
        else:
            module.fail_json(msg=msg, **result)


if __name__ == '__main__':
    main()
