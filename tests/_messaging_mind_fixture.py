"""Test-only mind for exercising the msg (MessageQueue) interface under
sandbox="subprocess" (#55). Not collected by pytest (module name doesn't
match the test_*.py pattern).
"""

import cells


class AgentMind:
    def __init__(self, cargs):
        pass

    def act(self, view, msg):
        msg.send_message("ping")
        return (
            cells.Action(cells.ACT_EAT)
            if msg.get_messages()
            else cells.Action(cells.ACT_SPAWN)
        )
