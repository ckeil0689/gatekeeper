import uuid
from pymongo import MongoClient
import pymongo.errors
from delorean import Delorean
from delorean import epoch
from datetime import timedelta
from errors import OperationFailure, GroupNameNotValid
from errors import ConnectionFailure
from errors import NotMasterError
from errors import ServiceNameNotValid
from errors import ServiceAlreadyExists
from errors import NotFound
from errors import TicketNotFound
from errors import GateStateNotValid
from errors import EnvironmentNotFound
from errors import JsonStructureError


class MongoConnect:
    def __init__(self, config):
        self.client = MongoClient(config['mongo']['uris'])
        self.db = self.client[config['mongo']['database']]
        self.collection = self.db[config['mongo']['collection']]
        self.tickets = self.db[config['mongo']['collection'] + '_tickets']
        self.d = Delorean()
        self.d = self.d.shift('Europe/Amsterdam')

    def create_new_gate(self, group, name, request):
        if not name or '.' in name:
            raise ServiceNameNotValid
        if not group or '.' in group:
            raise GroupNameNotValid

        if self.check_existence(group, name):
            raise ServiceAlreadyExists

        if 'environments' not in request or not request['environments'] or '' in request['environments'] or type(
                request['environments']) != list:
            raise JsonStructureError("Invalid environments.")
        try:
            data = dict()
            data['_id'] = str(uuid.uuid4())
            data['document_version'] = 2.2
            data['name'] = name.strip()
            data['group'] = group.strip()
            data['environments'] = dict()
            for env in request['environments']:
                env = env.strip()
                data['environments'][env] = dict()
                data['environments'][env]['state'] = 'open'
                data['environments'][env]['state_timestamp'] = self.get_formatted_timestamp()
                data['environments'][env]['message'] = ''
                data['environments'][env]['message_timestamp'] = ''
                data['environments'][env]['queue'] = []
            self.collection.insert_one(data)
            data.pop('_id')  # do not return _id  
            return data
        except pymongo.errors.NotMasterError as error:
            raise NotMasterError(error.message)

    def remove_gate(self, group, name):
        if not self.check_existence(group, name):
            raise NotFound
        try:
            self.collection.remove({"name": name, "group": group})
        except pymongo.errors.NotMasterError as error:
            raise NotMasterError(error.message)

    def get_gates(self, group=''):
        try:
            if group:
                return list(self.collection.find({'group': group}))
            else:
                return list(self.collection.find({}))
        except pymongo.errors.ConnectionFailure as error:
            raise ConnectionFailure(error.message)
        except pymongo.errors.OperationFailure as error:
            raise OperationFailure(error.message)

    def update_gate(self, group, name, entry):
        try:
            self.collection.update({"name": name, "group": group}, {'$set': entry}, upsert=False)
        except pymongo.errors.ConnectionFailure as error:
            raise ConnectionFailure(error.message)
        except pymongo.errors.OperationFailure as error:
            raise OperationFailure(error.message)

    def get_gate(self, group, name):
        entry = self.check_existence(group, name)
        if not entry:
            raise NotFound
        return entry

    def set_gate(self, group, name, environment, state):
        entry = self.check_existence(group, name)
        self.validate_environment_state(entry, environment, state)
        try:
            current_state = entry['environments'][environment]['state']
            if current_state != state:
                entry['environments'][environment]['state'] = state
                entry['environments'][environment]['state_timestamp'] = self.get_formatted_timestamp()
                return self.collection.update({'name': name, 'group': group}, {'$set': entry}, upsert=False)
        except pymongo.errors.NotMasterError as error:
            raise NotMasterError(error.message)

    @staticmethod
    def get_expiration_date(minutes_delta):
        expiration_date = Delorean.now()
        expiration_date += timedelta(minutes=minutes_delta)
        return expiration_date.epoch

    def add_ticket(self, group, name, environment, ticket):
        entry = self.check_existence(group, name)
        self.validate_environment_state(entry, environment, 'open')
        entry['environments'][environment]['queue'].append(ticket)
        try:
            self.collection.update({'name': name, 'group': group}, {'$set': entry}, upsert=False)
            mapping = self.get_ticket(ticket["id"])
            if not mapping:
                mapping = ticket.copy()
                mapping.update({"gates": [[group, name, environment]]})
            else:
                mapping['gates'].append([group, name, environment])
            self.tickets.update({"id": ticket["id"]}, {'$set': mapping}, upsert=True)
        except pymongo.errors.NotMasterError as error:
            raise NotMasterError(error.message)
        return ticket

    def get_ticket(self, ticket_id):
        return self.tickets.find_one({"id": ticket_id}, {'_id': False})

    def remove_ticket(self, ticket_id):
        mapping = self.get_ticket(ticket_id)
        if mapping:
            for gate_tripel in mapping['gates']:
                entry = self.check_existence(gate_tripel[0], gate_tripel[1])
                if entry and gate_tripel[2] in entry['environments']:
                    for t in entry['environments'][gate_tripel[2]]['queue']:
                        if t["id"] == ticket_id:
                            entry['environments'][gate_tripel[2]]['queue'].remove(t)
                            break
                    try:
                        self.collection.update({'group': gate_tripel[0], 'name': gate_tripel[1]}, {'$set': entry},
                                               upsert=False)
                    except pymongo.errors.NotMasterError as error:
                        raise NotMasterError(error.message)
            self.tickets.remove({"id": ticket_id})

    def set_ticket_expiration_date(self, ticket_id, expiration_date):
        mapping = self.get_ticket(ticket_id)
        if mapping:
            for gate_tupel in mapping['gates']:
                entry = self.check_existence(gate_tupel[0], gate_tupel[1])
                if entry and gate_tupel[2] in entry['environments']:
                    for i, t in enumerate(entry['environments'][gate_tupel[2]]['queue']):
                        if t["id"] == ticket_id:
                            entry['environments'][gate_tupel[2]]['queue'][i]["expiration_date"] = expiration_date
                            break
                    try:
                        self.collection.update({'group': gate_tupel[0], 'name': gate_tupel[1]}, {'$set': entry},
                                               upsert=False)
                    except pymongo.errors.NotMasterError as error:
                        raise NotMasterError(error.message)
            mapping['expiration_date'] = expiration_date
            self.tickets.update({"id": ticket_id}, {'$set': mapping}, upsert=False)
        else:
            raise TicketNotFound

    @staticmethod
    def validate_environment_state(entry, environment, state):
        if not entry:
            raise NotFound
        if environment not in entry['environments']:
            raise EnvironmentNotFound
        if state not in ["open", "closed"]:
            raise GateStateNotValid

    def set_message(self, group, name, environment, message):
        entry = self.check_existence(group, name)
        if not entry:
            raise NotFound
        if environment not in entry['environments']:
            raise EnvironmentNotFound
        try:
            entry['environments'][environment]['message'] = message
            entry['environments'][environment]['message_timestamp'] = self.get_formatted_timestamp() if message else ""
            return self.collection.update({"name": name, "group": group}, {'$set': entry}, upsert=False)
        except pymongo.errors.NotMasterError as error:
            raise NotMasterError(error.message)

    def check_existence(self, group, name):
        return self.collection.find_one({"name": name, "group": group}, {'_id': False})

    def get_groups(self):
        return self.collection.distinct('group')

    def get_formatted_timestamp(self):
        return self.d.now().format_datetime(format='y-MM-dd HH:mm:ssz')
