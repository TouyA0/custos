"""Fenêtre de la mascotte : surface gtk4-layer-shell transparente, au-dessus de tout."""

from custos.config import MascotConfig
from custos.states import MascotState


class MascotOverlay:
    def __init__(self, config: MascotConfig) -> None:
        self.config = config
        self.state = MascotState.IDLE

    def set_state(self, state: MascotState) -> None:
        raise NotImplementedError("ui.overlay.MascotOverlay.set_state")
