"""Everything the endpoints share for the life of the process."""

from dataclasses import dataclass
from typing import Literal

from voice_agent.api.ws import Hub
from voice_agent.config import Settings
from voice_agent.session.events import SessionStore
from voice_agent.session.manager import SessionManager
from voice_agent.understanding.llm import LlamaServer


@dataclass
class Runtime:
    settings: Settings
    store: SessionStore
    manager: SessionManager
    hub: Hub
    llm_server: LlamaServer | None

    def language_model_state(self) -> Literal["running", "down", "disabled"]:
        if not self.settings.llm.enabled:
            return "disabled"
        return "running" if self.llm_server is not None and self.llm_server.running else "down"
