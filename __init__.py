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
from mycroft import MycroftSkill
from mycroft.messagebus.message import Message
from mycroft.util.log import LOG
from mycroft.skills.common_iot_skill import \
    _BusKeys, \
    IoTRequest, \
    Thing, \
    Action, \
    Attribute
from typing import List
from uuid import uuid4

IOT_REQUEST_ID = "iot_request_id"

_ACTIONS = [action.name for action in Action]
_THINGS = [thing.name for thing in Thing]
_ATTRIBUTES = [attribute.name for attribute in Attribute]


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


class SkillIoTControl(MycroftSkill):

    def __init__(self):
        MycroftSkill.__init__(self)
        self._current_requests = dict()
        self._normalized_to_orignal_word_map = dict()

    def initialize(self):
        self.add_event(_BusKeys.RESPONSE, self._handle_response)
        self.add_event(_BusKeys.REGISTER, self._register_words)
        self.bus.emit(Message(_BusKeys.CALL_FOR_REGISTRATION, {}))

        intent = (IntentBuilder('IoTRequestWithEntityOrAction')
                    .one_of('ENTITY', *_THINGS)
                    .one_of(*_ACTIONS)
                    .optionally('SCENE')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestWithEntityAndAction')
                    .require('ENTITY')
                    .one_of(*_THINGS)
                    .one_of(*_ACTIONS)
                    .optionally('SCENE')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestWithEntityOrActionAndProperty')
                    .one_of('ENTITY', *_THINGS)
                    .one_of(*_ACTIONS)
                    .one_of(*_ATTRIBUTES)
                    .optionally('SCENE')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestWithEntityAndActionAndProperty')
                    .require('ENTITY')
                    .one_of(*_THINGS)
                    .one_of(*_ACTIONS)
                    .one_of(*_ATTRIBUTES)
                    .optionally('SCENE')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestScene')
                  .require('SCENE')
                  .one_of(*_ACTIONS)
                  .build())
        self.register_intent(intent, self._handle_iot_request)

    def stop(self):
        pass

    def _handle_response(self, message: Message):
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
                self._normalized_to_orignal_word_map[normalized] = word
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
        winners = self._pick_winners(candidates)
        for winner in winners:
            self.bus.emit(Message(
                _BusKeys.RUN + winner.data["skill_id"], winner.data))
        self.acknowledge()

    def _pick_winners(self, candidates: List[Message]):
        # TODO - make this actually pick winners
        return candidates

    def _get_enum_from_data(self, enum_class, data: dict):
        for e in enum_class:
            if e.name in data:
                return e
        return None

    @_handle_iot_request
    def _handle_iot_request(self, message: Message):
        data = self._clean_power_request(message.data)
        action = self._get_enum_from_data(Action, data)
        thing = self._get_enum_from_data(Thing, data)
        attribute = self._get_enum_from_data(Attribute, data)
        entity = data.get('ENTITY')
        scene = data.get('SCENE')
        original_entity = (self._normalized_to_orignal_word_map.get(entity)
                           if entity else None)
        original_scene = (self._normalized_to_orignal_word_map.get(scene)
                          if scene else None)

        self._trigger_iot_request(data, action, thing, attribute, entity, scene)

        if original_entity or original_scene:
            self._trigger_iot_request(data, action, thing, attribute,
                                      original_entity, original_scene)

        self._set_context(thing, entity, data)

    def _trigger_iot_request(self, data: dict,
                             action: Action,
                             thing: Thing=None,
                             attribute: Attribute=None,
                             entity: str=None,
                             scene: str=None):
        request = IoTRequest(
            action=action,
            thing=thing,
            attribute=attribute,
            entity=entity,
            scene=scene
        )

        LOG.info("Looking for handlers for: {request}".format(request=request))

        data[IoTRequest.__name__] = request.to_dict()

        self.bus.emit(Message(_BusKeys.TRIGGER, data))

    def _set_context(self, thing: Thing, entity: str, data: dict):
        if thing:
            self.set_context(thing.name, data[thing.name])
        if entity:
            self.set_context('ENTITY', entity)

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
    return SkillIoTControl()
