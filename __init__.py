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

# TODO Exceptions should be custom types

from collections import defaultdict, namedtuple
from enum import Enum

from adapt.intent import IntentBuilder
from mycroft import MycroftSkill
from mycroft.messagebus.message import Message
from mycroft.util.log import LOG
from mycroft.util.parse import extract_number
from mycroft.skills.common_iot_skill import \
    _BusKeys, \
    IoTRequest, \
    Thing, \
    Action, \
    Attribute, \
    State, \
    IOT_REQUEST_ID
from typing import List, Dict, DefaultDict
from uuid import uuid4


_QUERY_ACTIONS = [Action.BINARY_QUERY.name, Action.INFORMATION_QUERY.name]
_NON_QUERY_ACTIONS = [action.name for action in Action if action.name not in _QUERY_ACTIONS]
_THINGS = [thing.name for thing in Thing]
_ATTRIBUTES = [attribute.name for attribute in Attribute]
_STATES = [state.name for state in State]


class IoTRequestStatus(Enum):
    POLLING = 0
    RUNNING = 1


SpeechRequest = namedtuple('SpeechRequest', ["utterance", "args", "kwargs"])


class TrackedIoTRequest():

    def __init__(
            self,
            id: str,
            status: IoTRequestStatus = IoTRequestStatus.POLLING,
    ):
        self.id = id
        self.status = status
        self.candidates = []
        self.speech_requests: DefaultDict[str, List[SpeechRequest]] = defaultdict(list)


class SkillIoTControl(MycroftSkill):

    def __init__(self):
        MycroftSkill.__init__(self)
        self._current_requests: Dict[str, TrackedIoTRequest] = dict()
        self._normalized_to_orignal_word_map: Dict[str, str] = dict()

    def _handle_speak(self, message: Message):
        iot_request_id = message.data.get(IOT_REQUEST_ID)

        skill_id = message.data.get("skill_id")

        utterance = message.data.get("speak")
        args = message.data.get("speak_args")
        kwargs = message.data.get("speak_kwargs")

        speech_request = SpeechRequest(utterance, args, kwargs)

        if iot_request_id not in self._current_requests:
            LOG.warning("Dropping speech request from {skill_id} for"
                        " {iot_request_id} because we are not currently"
                        " tracking that iot request. SpeechRequest was"
                        " {speech_request}".format(
                skill_id=skill_id,
                iot_request_id=iot_request_id,
                speech_request=speech_request
            ))

        self._current_requests[iot_request_id].speech_requests[skill_id].append(speech_request)
        LOG.info(self._current_requests[iot_request_id].speech_requests[skill_id])

    def initialize(self):
        self.add_event(_BusKeys.RESPONSE, self._handle_response)
        self.add_event(_BusKeys.REGISTER, self._register_words)
        self.add_event(_BusKeys.SPEAK, self._handle_speak)
        self.bus.emit(Message(_BusKeys.CALL_FOR_REGISTRATION, {}))

        intent = (IntentBuilder('IoTRequestWithEntityOrAction')
                    .one_of('ENTITY', *_THINGS)
                    .one_of(*_NON_QUERY_ACTIONS)
                    .optionally('SCENE')
                    .optionally('TO')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestWithEntityAndAction')
                    .require('ENTITY')
                    .one_of(*_THINGS)
                    .one_of(*_NON_QUERY_ACTIONS)
                    .optionally('SCENE')
                    .optionally('TO')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestWithEntityOrActionAndProperty')
                    .one_of('ENTITY', *_THINGS)
                    .one_of(*_NON_QUERY_ACTIONS)
                    .one_of(*_ATTRIBUTES)
                    .optionally('SCENE')
                    .optionally('TO')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestWithEntityAndActionAndProperty')
                    .require('ENTITY')
                    .one_of(*_THINGS)
                    .one_of(*_NON_QUERY_ACTIONS)
                    .one_of(*_ATTRIBUTES)
                    .optionally('SCENE')
                    .optionally('TO')
                    .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestScene')
                  .require('SCENE')
                  .one_of(*_NON_QUERY_ACTIONS)
                  .build())
        self.register_intent(intent, self._handle_iot_request)

        intent = (IntentBuilder('IoTRequestStateQuery')
                    .one_of(*_QUERY_ACTIONS)
                    .one_of(*_THINGS, 'ENTITY')
                    .one_of(*_STATES, *_ATTRIBUTES)
                    .build())
        self.register_intent(intent, self._handle_iot_request)

    def stop(self):
        pass

    def _handle_response(self, message: Message):
        id = message.data.get(IOT_REQUEST_ID)
        # TODO these should be logged, not exceptions
        if not id:
            raise Exception("No id found!")
        if id not in self._current_requests:
            raise Exception("Request is not being tracked."
                            " This skill may have responded too late.")
        if self._current_requests[id].status != IoTRequestStatus.POLLING:
            raise Exception("Skill responded too late."
                            " Request is no longer POLLING.")
        self._current_requests[id].candidates.append(message)

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
        request = self._current_requests.get(id)

        if request is None:
            raise Exception("This id is not being tracked!")

        request.status = IoTRequestStatus.RUNNING
        candidates = request.candidates

        if not candidates:
            self.speak_dialog('no.skills.can.handle')
        else:
            winners = self._pick_winners(candidates)
            for winner in winners:
                self.bus.emit(Message(
                    _BusKeys.RUN + winner.data["skill_id"], winner.data))

            self.schedule_event(self._speak_or_acknowledge,
                                1,  # TODO make this timeout a setting
                                data={IOT_REQUEST_ID: id},
                                name="SpeakOrAcknowledge")

    def _speak_or_acknowledge(self, message: Message):
        id = message.data.get(IOT_REQUEST_ID)
        request = self._current_requests.get(id)

        LOG.info("srs {}".format(request.speech_requests))
        if not request.speech_requests:
            self.acknowledge()
        else:
            for skill_id, requests in request.speech_requests.items():
                for utterance, args, kwargs in requests:
                    self.speak(utterance, *args, **kwargs)

    def _delete_request(self, message: Message):
        id = message.data.get(IOT_REQUEST_ID)
        LOG.info("Delete request {id}".format(id=id))
        try:
            del(self._current_requests[id])
        except KeyError:
            pass


    def _pick_winners(self, candidates: List[Message]):
        # TODO - make this actually pick winners
        return candidates

    def _get_enum_from_data(self, enum_class, data: dict):
        for e in enum_class:
            if e.name in data:
                return e
        return None

    def _handle_iot_request(self, message: Message):
        id = str(uuid4())
        message.data[IOT_REQUEST_ID] = id
        self._current_requests[id] = TrackedIoTRequest(id)

        data = self._clean_power_request(message.data)
        action = self._get_enum_from_data(Action, data)
        thing = self._get_enum_from_data(Thing, data)
        attribute = self._get_enum_from_data(Attribute, data)
        state = self._get_enum_from_data(State, data)
        entity = data.get('ENTITY')
        scene = data.get('SCENE')
        value = None

        if action == Action.SET and 'TO' in data:
            value = extract_number(message.data['utterance'])
            # extract_number may return False:
            value = value if value is not False else None

        original_entity = (self._normalized_to_orignal_word_map.get(entity)
                           if entity else None)
        original_scene = (self._normalized_to_orignal_word_map.get(scene)
                          if scene else None)

        self._trigger_iot_request(
            data,
            action,
            thing,
            attribute,
            entity,
            scene,
            value,
            state
        )

        if original_entity or original_scene:
            self._trigger_iot_request(data, action, thing, attribute,
                                      original_entity or entity,
                                      original_scene or scene,
                                      state)

        self.schedule_event(self._delete_request,
                            10,  # TODO make this timeout based on the other timeouts
                            data={IOT_REQUEST_ID: id},
                            name="DeleteRequest")
        self.schedule_event(self._run,
                            1,  # TODO make this timeout a setting
                            data={IOT_REQUEST_ID: id},
                            name="RunIotRequest")

    def _trigger_iot_request(self, data: dict,
                             action: Action,
                             thing: Thing=None,
                             attribute: Attribute=None,
                             entity: str=None,
                             scene: str=None,
                             value: int=None,
                             state: State=None):
        LOG.info('state is {}'.format(state))
        request = IoTRequest(
            action=action,
            thing=thing,
            attribute=attribute,
            entity=entity,
            scene=scene,
            value=value,
            state=state
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
