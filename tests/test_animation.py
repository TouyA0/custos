import random

from custos.states import MascotState
from custos.ui.animation import (
    BLINK_DURATION,
    BLINK_SPRITE,
    FAST_FPS,
    FPS,
    POP_AMPLITUDE,
    POP_DURATION,
    SPRITES,
    TIMEOUTS,
    Animator,
)


def make(sleep_after: float = 300.0) -> Animator:
    return Animator(sleep_after, now=0.0, rng=random.Random(42))


def test_starts_idle() -> None:
    assert make().frame(0.0).sprite == SPRITES[MascotState.IDLE]


def test_blinks_briefly() -> None:
    animator = make()
    blink = animator.next_blink
    assert animator.frame(blink - 0.01).sprite == SPRITES[MascotState.IDLE]
    assert animator.frame(blink + BLINK_DURATION / 2).sprite == BLINK_SPRITE
    assert animator.frame(blink + BLINK_DURATION + 0.01).sprite == SPRITES[MascotState.IDLE]
    assert animator.next_blink > blink  # prochain clignement programmé


def test_no_blink_outside_idle() -> None:
    animator = make()
    animator.set_state(MascotState.THINKING, 0.0)
    sprites = {animator.frame(t / 10).sprite for t in range(200)}
    assert sprites == {SPRITES[MascotState.THINKING]}


def test_falls_asleep_then_wakes_up() -> None:
    animator = make(sleep_after=60)
    assert animator.frame(59).sprite != SPRITES[MascotState.SLEEPING]
    assert animator.frame(61).sprite == SPRITES[MascotState.SLEEPING]
    animator.set_state(MascotState.THINKING, 70)
    assert animator.frame(1000).sprite == SPRITES[MascotState.THINKING]  # pas de timeout


def test_talking_and_worried_return_to_idle() -> None:
    for state in (MascotState.TALKING, MascotState.WORRIED):
        animator = make()
        animator.set_state(state, 10.0)
        timeout = TIMEOUTS[state]
        assert animator.frame(10 + timeout - 0.1).sprite == SPRITES[state]
        animator.frame(10 + timeout + 0.1)
        assert animator.state is MascotState.IDLE


def test_idle_after_talking_still_falls_asleep() -> None:
    animator = make(sleep_after=60)
    animator.set_state(MascotState.TALKING, 0.0)
    animator.frame(TIMEOUTS[MascotState.TALKING] + 1)
    assert animator.frame(TIMEOUTS[MascotState.TALKING] + 61).sprite == SPRITES[MascotState.SLEEPING]


def test_pop_on_state_change() -> None:
    animator = make()
    animator.set_state(MascotState.TALKING, 5.0)
    assert animator.frame(5.0 + POP_DURATION / 2).scale == 1 + POP_AMPLITUDE
    assert animator.frame(5.0 + POP_DURATION + 0.01).scale == 1.0


def test_worried_shakes_then_stops() -> None:
    animator = make()
    animator.set_state(MascotState.WORRIED, 0.0)
    assert any(abs(animator.frame(t / 100).dx) > 0.005 for t in range(1, 30))
    assert animator.frame(2.0).dx == 0.0


def test_floating_stays_small() -> None:
    animator = make()
    assert all(abs(animator.frame(t / 10).dy) <= 0.03 for t in range(100))


def test_frame_rate_follows_state() -> None:
    animator = make(sleep_after=60)
    assert animator.fps(0.1) == FAST_FPS  # rebond de départ
    assert animator.fps(5) == FPS[MascotState.IDLE]
    animator.frame(61)  # endormie
    assert animator.fps(70) == FPS[MascotState.SLEEPING]
    assert FPS[MascotState.SLEEPING] < FPS[MascotState.IDLE] < FAST_FPS
