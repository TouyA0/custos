"""Bulle de dialogue : champ de saisie et réponse affichée au fil de l'eau."""


class Bubble:
    def show(self) -> None:
        raise NotImplementedError("ui.bubble.Bubble.show")

    def append(self, text: str) -> None:
        raise NotImplementedError("ui.bubble.Bubble.append")

    def hide(self) -> None:
        raise NotImplementedError("ui.bubble.Bubble.hide")
