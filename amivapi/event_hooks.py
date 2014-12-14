from flask import current_app as app
from flask import abort
from eve.utils import debug_error_message

import datetime as dt
import json

from amivapi import models, utils, confirm


def pre_users_get_callback(request, lookup):
    print('A GET request on the users endpoint has just been received!')


def post_users_get_callback(request, lookup):
    print('The GET request on the users endpoint has just been handled')


def pre_signups_post_callback(request):
    data = utils.parse_data(request)
    db = app.data.driver.session
    eventid = data.get('event_id')
    event = db.query(models.Event).get(eventid)
    if event is None:
        abort(422, description=debug_error_message(
            'The given event_id could not be found in /events'
        ))
    if data.get('user_id') == -1:
        if event.is_public is False:
            abort(422, description=debug_error_message(
                'The event is only open for registered users.'
            ))
        email = data.get('email')
        if email is None:
            abort(422, description=debug_error_message(
                'You need to provide an email-address or a valid user_id'
            ))
        alreadysignedup = db.query(models.EventSignup).filter(
            models.EventSignup.event_id == eventid,
            models.EventSignup.user_id == -1,
            models.EventSignup.email == email
        ).first() is not None
    else:
        userid = data.get('user_id')
        alreadysignedup = db.query(models.EventSignup).filter(
            models.EventSignup.event_id == eventid,
            models.EventSignup.user_id == userid
        ).first() is not None
    if alreadysignedup:
        abort(422, description=debug_error_message(
            'You are already signed up for this event, try to use PATCH'
        ))
    """update schema of additional_data"""
    extraSchema = event.additional_fields
    if extraSchema is not None:
        resource_def = app.config['DOMAIN']['eventsignups']
        resource_def['schema'].update({
            'extra_data': {
                'type': 'dict',
                'schema': json.loads(extraSchema),
                'nullable': False,
            }
        })
        if data.get('extra_data') is None:
            abort(422, description=debug_error_message(
                'event %d requires extra data: %s' % (eventid, extraSchema)
            ))


def preSignupsInsertCallback(items):
    for doc in items:
        confirm.confirmActions(
            condition=(doc['user_id'] == -1),
            ressource='eventsignups',
            method='POST',
            doc=doc,
            items=items,
            email_field='email',
        )
        if 'extra_data' in doc:
            extra = doc.get('extra_data')
            doc['extra_data'] = json.dumps(extra)


def post_signups_post_callback(request, payload):
    data = utils.parse_data(request)
    if data.get('user_id') == -1:
        confirm.return_status(payload)


def preForwardsInsertCallback(items):
    for doc in items:
        confirm.confirmActions(
            condition=True,
            ressource='forwards',
            method='POST',
            doc=doc,
            items=items,
            email_field='address',
        )


def postForwardsPostCallback(request, payload):
    confirm.return_status(payload)


def pre_permissions_post_callback(request):
    print "lol"
    data = utils.parse_data(request)
    print "lol"
    now = dt.datetime.now()
    print "lol"
    if data.get('expiry_date') < now.isoformat():
        print "lol"
        abort(422, description=debug_error_message(
            'expiry_date needs to be in the future'
        ))