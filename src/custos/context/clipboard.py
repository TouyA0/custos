"""Texte sélectionné (`wl-paste --primary`) et presse-papier (`wl-paste`)."""


def selection() -> str:
    raise NotImplementedError("context.clipboard.selection")


def clipboard() -> str:
    raise NotImplementedError("context.clipboard.clipboard")
