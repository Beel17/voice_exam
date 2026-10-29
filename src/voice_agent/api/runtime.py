"""Everything the endpoints share for the life of the process."""

from dataclasses import dataclass
from typing import Literal

from voice_agent.api.ws import Hub
from voice_agent.config import Settings
from voice_agent.runtime.pools import PoolStats
from voice_agent.session.events import SessionStore
from voice_agent.session.manager import SessionManager
from voice_agent.speech.stt import Recognizer, SherpaRecognizer
from voice_agent.speech.tts import SherpaSynthesizer, Synthesizer
from voice_agent.understanding.llm import LlamaServer


@dataclass
class Runtime:
    settings: Settings
    store: SessionStore
    manager: SessionManager
    hub: Hub
    llm_server: LlamaServer | None
    recognizer: Recognizer | None = None
    synthesizer: Synthesizer | None = None

    def speech_state(self) -> Literal["running", "down", "disabled"]:
        """Recognition: when it is down, candidates are moved to the keyboard."""
        return self._state(self.recognizer is not None)

    def voice_state(self) -> Literal["running", "down", "disabled"]:
        """Synthesis: when it is down, replies still reach the PC as text."""
        return self._state(self.synthesizer is not None)

    def _state(self, up: bool) -> Literal["running", "down", "disabled"]:
        if up:
            return "running"
        return "down" if self.settings.speech.enabled else "disabled"

    def recognition_stats(self) -> PoolStats | None:
        return self.recognizer.stats() if isinstance(self.recognizer, SherpaRecognizer) else None

    def synthesis_stats(self) -> PoolStats | None:
        if isinstance(self.synthesizer, SherpaSynthesizer):
            return self.synthesizer.stats()
        return None

    def language_model_state(self) -> Literal["running", "down", "disabled"]:
        if not self.settings.llm.enabled:
            return "disabled"
        return "running" if self.llm_server is not None and self.llm_server.running else "down"
