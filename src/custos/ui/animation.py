"""Animation de la mascotte, sans GTK : quel sprite afficher, et où, à un instant donné.

Les décalages sont exprimés en fraction de la taille de la mascotte, pour que
l'animation reste la même quelle que soit `mascot.size`.
"""

import math
import random
from dataclasses import dataclass

from custos.states import MascotState

BLINK_DURATION = 0.15  # secondes
BLINK_INTERVAL = (2.5, 6.0)  # attente aléatoire entre deux clignements
POP_DURATION = 0.25  # petit rebond quand l'état change
POP_AMPLITUDE = 0.08
SHAKE_DURATION = 0.6  # tremblement quand une commande échoue

# Retour automatique au repos (secondes) ; None = l'état dure jusqu'au prochain événement.
TIMEOUTS: dict[MascotState, float | None] = {
    MascotState.IDLE: None,
    MascotState.SLEEPING: None,
    MascotState.THINKING: None,
    MascotState.TALKING: 12.0,
    MascotState.WORRIED: 30.0,
}

# Flottement : (amplitude en fraction de la taille, période en secondes)
FLOAT: dict[MascotState, tuple[float, float]] = {
    MascotState.IDLE: (0.025, 3.0),
    MascotState.SLEEPING: (0.015, 5.0),
    MascotState.THINKING: (0.03, 1.2),
    MascotState.TALKING: (0.03, 0.9),
    MascotState.WORRIED: (0.02, 2.0),
}

# Images par seconde : moins quand le mouvement est lent, pour économiser le CPU.
FPS: dict[MascotState, int] = {
    MascotState.IDLE: 20,
    MascotState.SLEEPING: 10,
    MascotState.THINKING: 30,
    MascotState.TALKING: 30,
    MascotState.WORRIED: 20,
}
FAST_FPS = 30  # pendant le rebond et le tremblement

SPRITES = {
    MascotState.IDLE: "idle/0",
    MascotState.SLEEPING: "sleeping/0",
    MascotState.THINKING: "thinking/0",
    MascotState.TALKING: "talking/0",
    MascotState.WORRIED: "worried/0",
}
BLINK_SPRITE = "idle/1"

# Accrochée à une fenêtre, elle est assise sur son bord au repos et quand elle dort ;
# pour réfléchir, parler ou s'inquiéter, elle se lève (sprites debout).
SITTING_SPRITES = {
    MascotState.IDLE: "sitting/0",
    MascotState.SLEEPING: "sitting-sleeping/0",
}
SITTING_BLINK_SPRITE = "sitting/1"
ALL_SPRITES = [*SPRITES.values(), BLINK_SPRITE, *SITTING_SPRITES.values(), SITTING_BLINK_SPRITE]


@dataclass(frozen=True)
class Frame:
    sprite: str
    dx: float = 0.0
    dy: float = 0.0
    scale: float = 1.0


def snap(frame: Frame, pixels: float) -> Frame:
    """La frame arrondie au pixel près (`pixels` : taille de la mascotte en pixels réels).

    Le flottement ne fait que quelques pixels : la plupart des frames successives donnent
    la même image une fois arrondies, et il est inutile de la redessiner.
    """
    return Frame(
        frame.sprite,
        round(frame.dx * pixels) / pixels,
        round(frame.dy * pixels) / pixels,
        1 + round((frame.scale - 1) * pixels) / pixels,
    )


class Animator:
    def __init__(self, sleep_after: float, now: float, rng: random.Random | None = None) -> None:
        self.sleep_after = sleep_after
        self.rng = rng or random.Random()
        self.state = MascotState.IDLE
        self.since = now
        self.next_blink = now + self._blink_delay()
        self.sitting = False  # accrochée à une fenêtre : assise sur son bord quand elle le peut

    @property
    def is_sitting(self) -> bool:
        return self.sitting and self.state in SITTING_SPRITES

    def _blink_delay(self) -> float:
        return self.rng.uniform(*BLINK_INTERVAL)

    def set_state(self, state: MascotState, now: float) -> None:
        self.state = state
        self.since = now
        self.next_blink = now + self._blink_delay()

    def _update(self, now: float) -> None:
        """Transitions automatiques : retour au repos, puis endormissement."""
        elapsed = now - self.since
        timeout = TIMEOUTS[self.state]
        if timeout is not None and elapsed >= timeout:
            self.set_state(MascotState.IDLE, self.since + timeout)
        elif self.state is MascotState.IDLE and elapsed >= self.sleep_after:
            self.set_state(MascotState.SLEEPING, self.since + self.sleep_after)

    def fps(self, now: float) -> int:
        if now - self.since < max(POP_DURATION, SHAKE_DURATION):
            return FAST_FPS
        return FPS[self.state]

    def frame(self, now: float) -> Frame:
        self._update(now)
        elapsed = now - self.since

        sitting = self.is_sitting
        sprite = SITTING_SPRITES[self.state] if sitting else SPRITES[self.state]
        if self.state is MascotState.IDLE and now >= self.next_blink:
            if now < self.next_blink + BLINK_DURATION:
                sprite = SITTING_BLINK_SPRITE if sitting else BLINK_SPRITE
            else:
                self.next_blink = now + self._blink_delay()

        amplitude, period = FLOAT[self.state]
        dy = 0.0 if sitting else amplitude * math.sin(2 * math.pi * now / period)

        dx = 0.0
        if self.state is MascotState.WORRIED and elapsed < SHAKE_DURATION:
            fade = 1 - elapsed / SHAKE_DURATION
            dx = 0.02 * fade * math.sin(2 * math.pi * elapsed * 12)

        scale = 1.0
        if elapsed < POP_DURATION:
            scale = 1 + POP_AMPLITUDE * math.sin(math.pi * elapsed / POP_DURATION)

        return Frame(sprite, dx, dy, scale)
