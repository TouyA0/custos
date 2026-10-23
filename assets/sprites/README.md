# Sprites

Un dossier par état de la mascotte (voir `src/custos/states.py`), avec les images
de l'animation numérotées dans l'ordre :

```
idle/      0.png (repos)  1.png (clignement)
sleeping/  0.png
thinking/  0.png
talking/   0.png
worried/   0.png
sitting/   0.png (assise sur le bord d'une fenêtre)  1.png (clignement)
sitting-sleeping/  0.png
```

PNG transparents 512×512, tous cadrés pareil.

## Régénérer

Les images sources (générées avec ChatGPT, 1254×1254) se nomment `repos.png`,
`clignement.png`, `endormi.png`, `réfléchit.png`, `parle.png`, `inquiet.png`,
`assis.png`, `assis-clignement.png` et `assis-endormi.png`. Une image sans
transparence (fond noir) est détourée automatiquement.
Pour les recadrer et les ranger ici :

```bash
.venv/bin/python scripts/prepare_sprites.py ~/Downloads
```

Il génère aussi `assets/icon.png`, l'icône de la barre : à partir de `icone.png` si
présent, sinon en découpant la tête dans `repos.png`.

Le script applique le même cadre à toutes les images (la mascotte ne bouge pas
d'un état à l'autre) et efface le voile gris semi-transparent que le générateur
ajoute parfois, sans toucher à la lueur de la lanterne.

Si tu régénères les poses assises, vérifie `SEAT_LINE` dans `src/custos/ui/position.py` :
c'est la hauteur (fraction du sprite) où le corps s'arrête et où pendent les jambes,
posée pile sur le bord de la fenêtre.
