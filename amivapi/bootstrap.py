# -*- coding: utf-8 -*-
#
# license: AGPLv3, see LICENSE for details. In addition we strongly encourage
#          you to buy us beer if we meet and you like the software.

"""
Starting point for the API
"""

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from eve import Eve
from eve_sqlalchemy import SQL  # , ValidatorSQL
from eve_docs import eve_docs
from flask.ext.bootstrap import Bootstrap
from flask import g

from amivapi import (
    models,
    confirm,
    schemas,
    authentication,
    authorization,
    media,
    forwards,
    validation,
    ldap,
    documentation
)

from amivapi.utils import get_config


def create_app(disable_auth=False, **kwargs):
    """
    Create a new eve app object and initialize everything.

    :param disable_auth: This can be used to allow every request without
                         authentication for testing purposes
    :param **kwargs: All other parameters overwrite config values
    :returns: eve.Eve object, the app object
    """
    config = get_config()
    config['DOMAIN'] = schemas.get_domain()
    config['BLUEPRINT_DOCUMENTATION'] = documentation.get_blueprint_doc()
    config.update(kwargs)

    if disable_auth:
        app = Eve(settings=config,
                  data=SQL,
                  validator=validation.ValidatorAMIV,
                  media=media.FileSystemStorage)
    else:
        app = Eve(settings=config,
                  data=SQL,
                  validator=validation.ValidatorAMIV,
                  auth=authentication.TokenAuth,
                  media=media.FileSystemStorage)

    # Bind SQLAlchemy
    db = app.data.driver
    models.Base.metadata.bind = db.engine
    db.Model = models.Base

    Bootstrap(app)
    with app.app_context():
        g.db = db.session

    # Create LDAP connector
    if config['ENABLE_LDAP']:
        app.ldap_connector = ldap.LdapConnector(config['LDAP_USER'],
                                                config['LDAP_PASS'])

    # Generate and expose docs via eve-docs extension
    app.register_blueprint(eve_docs, url_prefix="/docs")
    app.register_blueprint(confirm.confirmprint)
    app.register_blueprint(authentication.authentication)
    app.register_blueprint(authorization.permission_info)
    app.register_blueprint(media.download)

    #
    #
    # Event hooks
    #
    # security note: hooks which are run before auth hooks should never change
    # the database
    #

    # authentication
    app.on_insert += authentication.set_author_on_insert
    app.on_replace += authentication.set_author_on_replace

    # authorization
    app.on_pre_GET += authorization.pre_get_permission_filter
    app.on_pre_POST += authorization.pre_post_permission_filter
    app.on_pre_PUT += authorization.pre_put_permission_filter
    app.on_pre_DELETE += authorization.pre_delete_permission_filter
    app.on_pre_PATCH += authorization.pre_patch_permission_filter
    app.on_update += authorization.update_permission_filter

    # Hooks for anonymous users
    app.on_insert_eventsignups += confirm.signups_confirm_anonymous
    app.on_insert_groupaddressmembers += confirm.\
        groupaddressmembers_insert_anonymous

    app.on_update += confirm.pre_update_confirmation
    app.on_delete_item += confirm.pre_delete_confirmation
    app.on_replace += confirm.pre_replace_confirmation

    # users
    app.on_pre_GET_users += authorization.pre_users_get
    app.on_pre_PATCH_users += authorization.pre_users_patch

    # Enrolling for email list: Authorization filters
    app.on_insert_groupaddressmembers += (authorization
                                          .group_public_check)
    app.on_insert_groupusermembers += (authorization
                                       .group_public_check)

    # email-management
    app.on_deleted_item_forwardaddresses += forwards.on_forwardaddress_deleted
    app.on_inserted_groupusermembers += forwards.on_groupusermember_inserted
    app.on_replaced_groupusermembers += forwards.on_groupusermember_replaced
    app.on_updated_groupusermembers += forwards.on_groupusermember_updated
    app.on_deleted_item_groupusermembers += forwards.on_groupusermember_deleted
    app.on_inserted_groupaddressmembers += \
        forwards.on_groupaddressmember_inserted
    app.on_replaced_groupaddressmembers += \
        forwards.on_groupaddressmember_replaced
    app.on_updated_groupaddressmembers += \
        forwards.on_groupaddressmember_updated
    app.on_deleted_item_groupaddressmembers += \
        forwards.on_groupaddressmember_deleted

    # EVENTSIGNUPS
    # Hooks to move 'email' to '_unregistered_email' after db access
    app.on_insert_eventsignups += confirm.replace_email_insert
    app.on_update_eventsignups += confirm.replace_email_update
    app.on_replace_eventsignups += confirm.replace_email_replace

    # Hooks to move '_unregistered_email' to 'email' after db access
    app.on_inserted_eventsignups += confirm.replace_email_inserted
    app.on_fetched_item_eventsignups += confirm.replace_email_fetched_item
    app.on_fetched_resource_eventsignups += (confirm
                                             .replace_email_fetched_resource)
    app.on_replaced_eventsignups += confirm.replace_email_replaced
    app.on_updated_eventsignups += confirm.replace_email_updated

    # Hooks to remove tokens from output
    app.on_inserted_eventsignups += confirm.remove_token_inserted
    app.on_fetched_item_eventsignups += confirm.remove_token_fetched_item
    app.on_fetched_resource_eventsignups += (confirm
                                             .remove_token_fetched_resource)
    app.on_replaced_eventsignups += confirm.remove_token_replaced

    return app


def init_database(connection, config):
    """Create tables and fill with initial anonymous and root user

    Throws sqlalchemy.exc.OperationalError(sqlite) or
    sqlalchemy.exc.ProgrammingError(mysql) if tables already exist

    :param connection: A database connection
    :param config: The configuration dictionary
    """
    # Tell MySQL to not treat 0 as NULL
    if connection.engine.url.drivername == "mysql":
        connection.execute("SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO'")

    try:
        models.Base.metadata.create_all(connection, checkfirst=False)
    except (OperationalError, ProgrammingError):
        print("Creating tables failed. Make sure the database does not exist" +
              " already!")
        raise

    root_user = models.User(
        id=0,
        _author=None,
        _etag='d34db33f',  # We need some etag, not important what it is
        password=u"root",
        firstname=u"Lord",
        lastname=u"Root",
        gender="male",
        email=config['ROOT_MAIL'],
        membership="none"
    )
    anonymous_user = models.User(
        id=-1,
        _author=root_user.id,
        _etag='4l3x15F4G',
        password=u"",
        firstname=u"Anon",
        lastname=u"X",
        gender="male",
        email=u"nobody@example.com",
        membership="none"
    )

    session = Session(bind=connection)
    session.add_all([root_user, anonymous_user])
    session.commit()
