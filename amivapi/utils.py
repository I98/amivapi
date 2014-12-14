import datetime as dt
import json

from eve.methods.common import payload

from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from flask import current_app as app

from amivapi import models
from amivapi.auth import create_new_hash


def init_database(connection, config):
    try:
        models.Base.metadata.create_all(connection, checkfirst=False)
    except OperationalError:
        print("You are trying to create a new database, but the database " +
              "already exists!")
        exit(0)

    session = Session(bind=connection)

    root = models.User(
        id=0,
        _author=0,
        username="root",
        password=create_new_hash(u"root"),
        firstname=u"Lord",
        lastname=u"Root",
        gender="male",
        email=config['ROOT_MAIL'],
        membership="none"
    )
    session.add(root)

    anonymous = models.User(
        id=-1,
        _author=0,
        username="anonymous",
        password=create_new_hash(u""),
        firstname=u"Anon",
        lastname=u"X",
        gender="male",
        email=u"nobody@example.com",
        membership="none"
    )
    session.add(anonymous)

    session.commit()


# if the data is in json format it will not be parsed into request.form
# therefore we make one object for both cases which we can just use
def parse_data(request):
    with request:
        data = payload()
    return data


class DateTimeDecoder(json.JSONDecoder):

    def __init__(self, *args, **kargs):
        json.JSONDecoder.__init__(
            self, object_hook=self.dict_to_object,
            *args, **kargs
        )

    def dict_to_object(self, d):
        if '__type__' not in d:
            return d

        type = d.pop('__type__')
        try:
            dateobj = dt(**d)
            return dateobj
        except:
            d['__type__'] = type
            return d


class DateTimeEncoder(json.JSONEncoder):
    """ Instead of letting the default encoder convert datetime to string,
        convert datetime objects into a dict, which can be decoded by the
        DateTimeDecoder
    """

    def default(self, obj):
        if isinstance(obj, dt.datetime):
            return {
                '__type__': 'datetime',
                'year': obj.year,
                'month': obj.month,
                'day': obj.day,
                'hour': obj.hour,
                'minute': obj.minute,
                'second': obj.second,
                'microsecond': obj.microsecond,
            }
        else:
            return json.JSONEncoder.default(self, obj)