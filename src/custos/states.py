"""États de la mascotte : chacun correspond à une animation."""

from enum import Enum


class MascotState(Enum):
    IDLE = "idle"          # veille, rien à signaler
    SLEEPING = "sleeping"  # inactivité prolongée
    THINKING = "thinking"  # en attente de la réponse de Claude
    TALKING = "talking"    # bulle de réponse ouverte
    WORRIED = "worried"    # une commande vient d'échouer
