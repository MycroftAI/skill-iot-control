from mycroft import MycroftSkill, intent_file_handler


class SkillIotControl(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)

    @intent_file_handler('control.iot.skill.intent')
    def handle_control_iot_skill(self, message):
        self.speak_dialog('control.iot.skill')


def create_skill():
    return SkillIotControl()

