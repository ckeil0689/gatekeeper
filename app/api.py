import json
import uuid

import config
import util
from delorean import Delorean
from errors import EnvironmentNotFound
from errors import ServiceAlreadyExists
from errors import ServiceNameNotValid
from errors import NotFound
from errors import GateStateNotValid
from errors import JsonStructureError
from errors import JsonValidationError
from errors import NotMasterError
from errors import TicketNotFound
from flask import Response, request, Blueprint

blueprint = Blueprint('api', __name__)
blueprint.mongo = None


@blueprint.route('/api/gates', methods=['PUT'])
def api_test_and_set():
    try:
        status = "ok"
        data = util.data_from_request()
        ticket_id = (data["ticket"] if "ticket" in data else None)
        ticket = (blueprint.mongo.get_ticket(ticket_id) if ticket_id else None)

        if ticket_id and not ticket:
            raise TicketNotFound
        if ticket:
            date = Delorean.now().epoch
            if ticket['expiration_date'] != 0 and ticket['expiration_date'] < date:
                raise TicketNotFound  # TODO the ticket was found, but was not valid anymore
        if "gates" not in data:
            raise JsonStructureError("Could not find gates")  # TODO extract string into errors

        for group, services in data['gates'].iteritems():
            for service, environments in services.iteritems():
                entry = blueprint.mongo.get_gate(group, service)
                if type(environments) != type(list()):
                    environments = [environments]
                for env in environments:
                    if check_gate(entry, env, ticket_id):
                        if request.args and request.args['queue']:
                            status = "queued"
                            break
                        return Response('{"status": "denied"}', status=200, mimetype='application/json')
        if status == "queued":
            expiration_date = blueprint.mongo.get_expiration_date(config.QUEUED_TICKET_LIFETIME)
        else:
            expiration_date = blueprint.mongo.get_expiration_date(
                config.CURRENT_TICKET_LIFETIME) if config.CURRENT_TICKET_LIFETIME != 0 else 0
        if not ticket:
            ticket = {"id": str(uuid.uuid4()),
                      "updated": Delorean.now().format_datetime(format='y-MM-dd HH:mm:ssz'),
                      "expiration_date": expiration_date,
                      "link": data["link"] if "link" in data else None}
            for group, services in data['gates'].iteritems():
                for service, environments in services.iteritems():
                    if type(environments) != type(list()):
                        environments = [environments]
                    for env in environments:
                        blueprint.mongo.add_ticket(group, service, env, ticket)
                        response = {
                            "status": status,
                            "ticket": ticket
                        }
        else:
            ticket.update({"expiration_date": expiration_date})
            ticket.update({"updated": Delorean.now().format_datetime(format='y-MM-dd HH:mm:ssz')})
            blueprint.mongo.set_ticket_expiration_date(ticket["id"], expiration_date)
            response = {
                "status": status,
                "ticket": ticket
            }

        return Response(json.dumps(response), status=200, mimetype='application/json')
    except (NotFound, NotMasterError, ServiceAlreadyExists, ServiceNameNotValid, JsonValidationError,
            JsonStructureError,
            TicketNotFound) as error:
        return error_response(error)


@blueprint.route('/api/gates/<string:group>/<string:name>', methods=['POST'])
def api_new_gate(group, name):
    try:
        data = util.data_from_request()
        entry = blueprint.mongo.create_new_gate(group, name, data)
        return Response(json.dumps(entry), status=200, mimetype='application/json')
    except (
            NotMasterError, ServiceAlreadyExists, ServiceNameNotValid, JsonValidationError,
            JsonStructureError) as error:
        return error_response(error)


@blueprint.route('/api/gates/<string:group>/<string:name>', methods=['GET'])
@blueprint.route('/api/gates/<string:group>/<string:name>/<string:environment>', methods=['GET'])
def api_get_gate(group, name, environment=None):
    try:
        entry = blueprint.mongo.get_gate(group, name)
        if environment and environment not in entry['environments']:
            raise EnvironmentNotFound
        for env in entry['environments']:
            if check_gate(entry, env):
                entry['environments'][env]['state'] = "closed"
        if environment:
            entry = entry['environments'][environment]
        return Response(json.dumps(entry), status=200, mimetype='application/json')
    except (NotFound, EnvironmentNotFound) as error:
        return error_response(error)


@blueprint.route('/api/gates/<string:group>/<string:name>', methods=['PUT'])
@blueprint.route('/api/gates/<string:group>/<string:name>/<string:environment>', methods=['PUT'])
def api_update_gate(group, name, environment=None):
    try:
        data = util.data_from_request()
        entry = blueprint.mongo.get_gate(group, name)

        if "group" in data:
            entry["group"] = data["group"]
            blueprint.mongo.update_gate(group, name, entry)
        if "name" in data:
            entry["name"] = data["name"]
            blueprint.mongo.update_gate(group, name, entry)
            name = data["name"]

        if environment:
            if "state" in data:
                blueprint.mongo.set_gate(group, name, environment, data["state"])
            if "message" in data:
                blueprint.mongo.set_message(group, name, environment, data["message"])
        else:
            if "environments" in data:
                for env in data["environments"]:
                    if "state" in data["environments"][env]:
                        blueprint.mongo.set_gate(group, name, env, data["environments"][env]["state"])
                    if "message" in data["environments"][env]:
                        blueprint.mongo.set_message(group, name, env, data["environments"][env]["message"])
        entry = blueprint.mongo.get_gate(group, name)
        return Response(json.dumps(entry), status=200, mimetype='application/json')
    except (
            NotMasterError, ServiceNameNotValid, NotFound, GateStateNotValid, EnvironmentNotFound,
            JsonValidationError,
            JsonStructureError) as error:
        return error_response(error)


@blueprint.route('/api/gates/<string:name>', methods=['DELETE'])
@blueprint.route('/api/gates/<string:group>/<string:name>', methods=['DELETE'])
def api_remove_gate(group, name):
    try:
        blueprint.mongo.remove_gate(group, name)
        return Response('{"status": "ok"}', status=200, mimetype='application/json')
    except(NotFound, NotMasterError) as error:
        return error_response(error)


@blueprint.route('/api/tickets/<string:ticket_id>', methods=['DELETE'])
def api_release(ticket_id):
    try:
        blueprint.mongo.remove_ticket(ticket_id)
        return Response('{"status": "ok"}', status=200, mimetype='application/json')
    except (NotMasterError, ServiceAlreadyExists, ServiceNameNotValid, JsonValidationError, JsonStructureError,
            TicketNotFound) as error:
        return error_response(error)


def check_gate(entry, env, ticket_id=None):
    clean_queue(entry['environments'][env]['queue'])
    return env not in entry['environments'] or \
           entry['environments'][env]['state'] == 'closed' or \
           queue_is_blocked(entry['environments'][env]['queue'], ticket_id) or \
           (env in blueprint.config and not util.are_manual_settings_observed(blueprint.config, env))


def queue_is_blocked(queue, ticket_id=None):
    if not queue:
        return False
    queue = clean_queue(queue)
    date = Delorean.now().epoch
    for t in queue:
        if ticket_id and t["id"] == ticket_id:
            if t["expiration_date"] == 0 or t["expiration_date"] > date:
                return False
        if t["expiration_date"] == 0 or t["expiration_date"] > date:
            return True
    return False


def clean_queue(queue):
    date = Delorean.now().epoch
    for t in queue:
        if t["expiration_date"] != 0 and t["expiration_date"] < date:
            queue.remove(t)
            try:
                blueprint.mongo.remove_ticket(t["expiration_date"])
            except TicketNotFound:
                pass
    return queue


def error_response(exception):
    return Response('{"status": "error", "reason": "' + exception.message + '"}',
                    status=exception.status_code if getattr(exception, 'status_code', None) else 400,
                    mimetype='application/json')
