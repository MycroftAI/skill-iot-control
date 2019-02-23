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
from mycroft.util.log import getLogger
from mycroft.skills.common_iot_skill import BusKeys, IoTRequest, Thing, Action
from uuid import uuid4

__author__ = 'ChristopherRogers1991'

LOGGER = getLogger(__name__)

IOT_REQUEST_ID = "iot_request_id"


def _handle_iot_request(handler_function):
    def tracking_intent_handler(self, message):
        id = str(uuid4())
        message.data[IOT_REQUEST_ID] = id
        self._current_requests[id] = []
        handler_function(self, message)
        self.schedule_event(self._run,
                            5,
                            data={IOT_REQUEST_ID: id},
                            name="RunIotRequest")
    return tracking_intent_handler


class SkillIotControl(MycroftSkill):

    def __init__(self):
        MycroftSkill.__init__(self)
        self._current_requests = dict()

    def initialize(self):
        self.add_event(BusKeys.RESPONSE, self._handle_response)

    def _handle_response(self, message: Message):
        LOGGER.info("Message data was: " +  str(message.data))
        id = message.data.get(IOT_REQUEST_ID)
        if not id:
            raise Exception("No id found!")
        if not id in self._current_requests:
            raise Exception("Request is not being tracked."
                            " This skill may have responded too late.")
        self._current_requests[id].append(message)

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
        self.bus.emit(Message(BusKeys.RUN + winner.data["skill_id"], winner.data))

    def _pick_winner(self, candidates: [Message]):
        # TODO - make this actually pick a winner
        winner = candidates[0]
        return winner

    @intent_handler(IntentBuilder('LightsOn')
                    .require('Lights')
                    .require('On'))
    @_handle_iot_request
    def handle_lights_on(self, message: Message):
        self.speak("Lights on request")
        data = message.data

        request = IoTRequest(
            action=Action.ON,
            thing=Thing.LIGHT,
            entity=data.get('Entity'),
            scene=None
        )

        data[IoTRequest.__name__] = repr(request)

        self.bus.emit(Message(BusKeys.TRIGGER, data))


def create_skill():
    return SkillIotControl()

