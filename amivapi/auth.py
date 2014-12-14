import hashlib
import json
import rsa
from os import urandom
from datetime import datetime
from base64 import b64encode, b64decode

from flask import current_app as app
from flask import Blueprint, abort, request, g

from eve.render import send_response
from eve.methods.post import post_internal
from eve.auth import TokenAuth

from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.inspection import inspect

import models
import permission_matrix
import utils

"""
This file provides token based authentification. A user can POST the /sessions
resource to obtain a token, which is a json dict of the following form:

{
    'user_id': integer,
    'login_time': time,
    'signature': string(64 bytes hex = 256 Bit SHA2)
}

This token shows that he is the user identified by user_id and obtained the
token at the time in login_time.
The signature is the SHA2 hash of user_id padded with zeros to 10 digits
followed by the time of login in the DATE_FORMAT specified in the config
followed by the servers login secret(see TokenAuth._create_signature)
"""


def _create_signature(user_id, login_time):
    msg = "{:0=10d}".format(user_id) \
          + login_time.strftime(app.config['DATE_FORMAT'])

    return b64encode(rsa.sign(msg, app.config['LOGIN_PRIVATE_KEY'], 'SHA-256'))


""" Creates a new hash for a password. This generates a random salt, so it can
not be used to check hashes!
"""


def create_new_hash(password):
    salt = urandom(16)
    password = bytearray(password, 'utf-8')
    return (
        b64encode(salt) +
        '$' +
        b64encode(hashlib.pbkdf2_hmac('SHA256', password, salt, 100000))
    )

""" Creates a new token for the specified user. The new token is based on the
current time, so the return value will change every second.
"""


def createToken(user_id):
    time = datetime.now()
    signature = _create_signature(user_id, time)

    return b64encode(json.dumps({
        'user_id': user_id,
        'login_time': time.strftime(app.config['DATE_FORMAT']),
        'signature': signature,
    }))


""" Utility function to get class associated with a resource """


def getClassForResource(resource):
    resource_def = app.config['DOMAIN'][resource]
    return getattr(models, resource_def['datasource']['source'])


""" This function (HACK ALERT) tries to figure out where relationship would
point to if an object was created with the passed request. If somebody finds
a better way to check permissions please consider changing this. We depend
on a lot of knowledge of relationship internals. """


def resolve_future_field(resource, request, field):
    resource_class = getClassForResource(resource)

    field_parts = field.split('.')  # This looks like an emoticon

    if len(field_parts) == 1:
        return request.form[field]

    relationship = inspect(resource_class).relationships[field_parts[0]]

    query = app.data.driver.session.query(relationship.target)
    for l, r in relationship.local_remote_pairs:
        query = query.filter(r.__eq__(request.form[l.name]))

    value = query.one()

    for part in field_parts[1:]:
        value = getattr(value, part)

    return value


""" Beginning of actual authentification process """


class TokenAuth(TokenAuth):
    """ We could have used eve's allowed_roles parameter, but that does not
    support roles on endpoint level, but only on resource level """
    def check_auth(self, token, allowed_roles, resource, method):
        dbsession = app.data.driver.session

        try:
            sess = dbsession.query(models.Session).filter(
                models.Session.token == token).one()
        except NoResultFound:
            abort(401)

        g.logged_in_user = sess.user_id

        if sess.user_id == 0:
            g.resource_admin_access = True
            return True

        """ Check if user has endpoint admin access
        (so can perform this method with any parameters) """
        g.resource_admin_access = False

        permissions = dbsession.query(models.Permission) \
            .filter(models.Permission.user_id == sess.user_id).all()
        for permission in permissions:
            try:
                if permission_matrix. \
                        roles[permission.role][resource][method] == 1:
                    g.resource_admin_access = True
                    return True
            except KeyError:
                pass

        """ User does not have admin access, check if he might still
        perform the action """

        cls = getClassForResource(resource)
        if hasattr(cls, '__affected_user__') or hasattr(cls, '__owner__'):
            """ In this case the permissions are per object """
            return True

        return False


""" Authentification related endpoints """

auth = Blueprint('auth', __name__)


""" Handle POST to /sessions

A POST to /sessions exspects a username and password. If they are correct a
token is created and used to register a session in the database, which is sent
back to the user.

If the user is not found we try to import the user via LDAP, if he is found we
update his data
"""


@auth.route('/sessions', methods=['POST'])
def process_login():
    user = app.data.driver.session.query(models.User).filter_by(
        username=request.form['username']).all()

    if(len(user) == 1):
        (salt, hashed_password) = user[0].password.split('$')
        salt = b64decode(salt)
        hashed_password = b64decode(hashed_password)
        sent_password = bytearray(utils.parse_data(request)['password'], 'utf-8')

        if hashed_password != hashlib.pbkdf2_hmac(
                'SHA256',
                sent_password,
                salt,
                100000
        ):
            abort(401)

        token = createToken(user[0].id)
        response = post_internal(
            'sessions',
            {
                'user_id': user[0].id,
                'token': token
            }
        )
        return send_response('sessions', response)

    # Try to import user via ldap
    abort(501)


""" Auth related hooks """


""" Permission filters for all requests """


def pre_get_permission_filter(resource, request, lookup):
    if g.resource_admin_access:
        return

    abort(501)


def pre_post_permission_filter(resource, request):
    if g.resource_admin_access:
        return

    resource_class = getClassForResource(resource)
    try:
        allowed_id_field = getattr(resource_class, '__owner__')
    except AttributeError:
        abort(403)

    if resolve_future_field(resource, request, allowed_id_field) \
            != g.logged_in_user:
        abort(403)


def pre_put_permission_filter(resource, request, lookup):
    if g.resource_admin_access:
        return

    abort(501)


def pre_patch_permission_filter(resource, request, lookup):
    if g.resource_admin_access:
        return

    abort(501)


def pre_delete_permission_filter(resource, request, lookup):
    if g.resource_admin_access:
        return

    abort(501)


""" Hooks to add _author field to all database inserts """


def set_author_on_insert(resource, items):
    _author = getattr(g, 'logged_in_user', -1)
    for i in items:
        i['_author'] = _author


def set_author_on_replace(resource, items, original):
    set_author_on_insert(resource, items)


""" Hooks to hash passwords when user entries are changed in the database """


def hash_password_before_insert(users):
    for u in users:
        u['password'] = create_new_hash(u['password'])


def hash_password_before_update(users):
    hash_password_before_insert(users)


def hash_password_before_replace(users, original_users):
    hash_password_before_insert(users)