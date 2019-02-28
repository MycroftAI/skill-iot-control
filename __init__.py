# Copyright 2019 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from adapt.intent import IntentBuilder
from mycroft import MycroftSkill, intent_handler
from mycroft.messagebus.message import Message
from mycroft.util.log import LOG
from mycroft.skills.common_iot_skill import _BusKeys, IoTRequest, Thing, Action
from uuid import uuid4

IOT_REQUEST_ID = "iot_request_id"

_ACTIONS = [action.name for action in Action]
_THINGS = [thing.name for thing in Thing]


# TODO Exceptions should be custom types
# TODO more intent handlers


def _handle_iot_request(handler_function):
    def tracking_intent_handler(self, message):
        id = str(uuid4())
        message.data[IOT_REQUEST_ID] = id
        self._current_requests[id] = []
        handler_function(self, message)
        self.schedule_event(self._run,
                            1,  # TODO make this timeout a setting
                            data={IOT_REQUEST_ID: id},
                            name="RunIotRequest")
    return tracking_intent_handler


class SkillIoTControl(MycroftSkill):

    def __init__(self):
        MycroftSkill.__init__(self)
        self._current_requests = dict()

    def initialize(self):
        self.add_event(_BusKeys.RESPONSE, self._handle_response)
        self.add_event(_BusKeys.REGISTER, self._register_words)
        self.bus.emit(Message(_BusKeys.CALL_FOR_REGISTRATION, {}))

    def _handle_response(self, message: Message):
        LOG.info("Message data was: " + str(message.data))
        id = message.data.get(IOT_REQUEST_ID)
        if not id:
            raise Exception("No id found!")
        if not id in self._current_requests:
            raise Exception("Request is not being tracked."
                            " This skill may have responded too late.")
        self._current_requests[id].append(message)

    def _register_words(self, message: Message):
        # TODO these will need to be normalized, and we will
        #  have to keep a map of the normalized values to the
        #  original. This is because user provided values may
        #  be things like "Master-Bedroom" which when spoken
        #  will translate to "master bedroom."
        type = message.data["type"]
        words = message.data["words"]

        for word in words:
            self.register_vocabulary(word, type)

    def _run(self, message: Message):
        id = message.data.get(IOT_REQUEST_ID)
        candidates = self._current_requests.get(id)

        if candidates is None:
            raise Exception("This id is not being tracked!")

        if not candidates:
            self.speak_dialog('no.skills.can.handle')
            return

        del(self._current_requests[id])
        winner = self._pick_winner(candidates)
        LOG.info("Winner data is: " + str(winner.data))
        self.bus.emit(Message(_BusKeys.RUN + winner.data["skill_id"], winner.data))

    def _pick_winner(self, candidates: [Message]):
        # TODO - make this actually pick a winner
        winner = candidates[0]
        return winner

    def _get_action_from_data(self, data: dict):
        for action in Action:
            if action.name in data:
                return action
        raise Exception("No action found!")

    def _get_thing_from_data(self, data: dict):
        for thing in Thing:
            if thing.name in data:
                return thing
        return None

    # TODO - generic requests may need to pick winners differently than
    #  other requests. May have to always ask which skill, if more than
    #  one can handle.
    @intent_handler(IntentBuilder('IoTRequestWithEntityOrAction')
                    .one_of('ENTITY', *_THINGS)
                    .one_of(*_ACTIONS)
                    .optionally('SCENE'))
    @_handle_iot_request
    def handle_iot_request_with_entity_or_thing(self, message: Message):
        self._handle_iot_request(message)

    @intent_handler(IntentBuilder('IoTRequestWithEntityAndAction')
                    .require('ENTITY')
                    .one_of(*_THINGS)
                    .one_of(*_ACTIONS)
                    .optionally('SCENE'))
    @_handle_iot_request
    def handle_iot_request_with_entity_and_thing(self, message: Message):
        self._handle_iot_request(message)

    def _handle_iot_request(self, message: Message):
        self.speak("IoT request")
        data = self._clean_power_request(message.data)
        action = self._get_action_from_data(data)
        thing = self._get_thing_from_data(data)

        request = IoTRequest(
            action=action,
            thing=thing,
            entity=data.get('ENTITY'),
            scene=data.get('SCENE')
        )

        data[IoTRequest.__name__] = request.to_dict()

        self.bus.emit(Message(_BusKeys.TRIGGER, data))

    def _clean_power_request(self, data: dict):
        """
        Clean requests that include a toggle word and a definitive value.

        Requests like "toggle the lights off" should only send "off"
        through as the action.

        Args:
            data: dict

        Returns:
            dict

        """
        if 'TOGGLE' in data and ('ON' in data or 'OFF' in data):
            del(data['TOGGLE'])
        return data


def create_skill():
    return SkillIoTControl()
