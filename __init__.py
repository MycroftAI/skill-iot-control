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
from mycroft.skills.common_iot_skill import _BusKeys, IoTRequest, Thing, Action
from mycroft.util.log import getLogger
from typing import List
from uuid import uuid4

__author__ = 'ChristopherRogers1991'

LOG = getLogger(__name__)

IOT_REQUEST_ID = "iot_request_id"

_ACTIONS = [action.name for action in Action]
_THINGS = [thing.name for thing in Thing]


# TODO Exceptions should be custom types

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


class SkillIotControl(MycroftSkill):

    def __init__(self):
        MycroftSkill.__init__(self)
        self._current_requests = dict()
        self._normalized_to_orginal_word_map = dict()

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
        type = message.data["type"]
        words = message.data["words"]

        for word in words:
            self.register_vocabulary(word, type)
            normalized = _normalize_custom_word(word)
            if normalized != word:
                self._normalized_to_orginal_word_map[normalized] = word
                self.register_vocabulary(normalized, type)

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

    def _pick_winner(self, candidates: List[Message]):
        # TODO - make this actually pick a winner
        winner = candidates[0]
        return winner

    def _get_action_from_data(self, data: dict) -> Action:
        for action in Action:
            if action.name in data:
                return action
        raise Exception("No action found!")

    def _get_thing_from_data(self, data: dict) -> [Thing, None]:
        for thing in Thing:
            if thing.name in data:
                return thing
        return None

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
        entity = data.get('ENTITY')
        scene = data.get('SCENE')
        original_entity = (self._normalized_to_orginal_word_map.get(entity)
                           if entity else None)
        original_scene = (self._normalized_to_orginal_word_map.get(scene)
                          if scene else None)

        self._trigger_iot_request(data, action, thing, entity, scene)

        if original_entity or original_scene:
            self._trigger_iot_request(data, action, thing,
                                      original_entity, original_scene)

    def _trigger_iot_request(self, data: dict,
                             action: Action,
                             thing: Thing=None,
                             entity: str=None,
                             scene: str=None):
        request = IoTRequest(
            action=action,
            thing=thing,
            entity=entity,
            scene=scene
        )

        data[IoTRequest.__name__] = request.to_dict()

        self.bus.emit(Message(_BusKeys.TRIGGER, data))


    def _clean_power_request(self, data: dict) -> dict:
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


def _normalize_custom_word(word: str, to_space: str = '_-') -> str:
    word = word.lower()
    letters = list(word)
    for index, letter in enumerate(letters):
        if letter in to_space:
            letters[index] = ' '
    return ''.join(letters)



def create_skill():
    return SkillIotControl()
