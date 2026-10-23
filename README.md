# custos
Mascotte gardienne pour Hyprland, propulsée par Claude : elle veille sur ton écran et t'aide là où tu travailles.

*Custos* signifie « gardien » en latin, d'après la prière à l'ange gardien :
*Angele Dei, qui custos es mei.*

## Principe

- Une petite mascotte vit dans un coin de l'écran (overlay GTK4 + gtk4-layer-shell).
- Un raccourci t'ouvre une bulle pour lui parler, ou l'interroge sur le texte sélectionné.
- Claude est appelé via `claude -p` : ça utilise ton abonnement Claude, pas de clé API.
- Claude n'est appelé que quand **tu** le demandes. Les réactions automatiques
  (une commande qui échoue, par exemple) restent locales.

## Prérequis (Arch Linux)

```bash
sudo pacman -S python python-gobject gtk4 gtk4-layer-shell grim slurp wl-clipboard
```

Et [Claude Code](https://claude.com/claude-code), connecté à ton compte (`claude /login`).

## Installation (développement)

```bash
python -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
mkdir -p ~/.config/custos && cp config/config.example.toml ~/.config/custos/config.toml
```

Lancer et gérer la mascotte :

```bash
custos start      # démarre en arrière-plan (custos daemon : au premier plan)
custos restart    # après une modification du code
custos stop
custos status
custos logs -f    # suit le log : ~/.local/state/custos/custos.log
```

`custos daemon` est un petit superviseur : il lance le vrai daemon (avec le
`LD_PRELOAD=libgtk4-layer-shell.so` nécessaire depuis Python) et le relance s'il plante,
jusqu'à 5 fois par minute. Le log ne contient jamais le contenu de tes questions,
sélections ou commandes : seulement des événements, des tailles et des durées.

Glisse la mascotte où tu veux : sa position est mémorisée (`~/.local/state/custos/position.json` ;
supprime ce fichier puis `custos restart` pour revenir au coin choisi dans la config).

Clic droit sur la mascotte : **S'accrocher** à la fenêtre où tu étais (ou `custos attach`).
Elle s'assoit sur le bord haut de la fenêtre, près du coin haut-gauche, et se lève quand
elle réfléchit, parle ou s'inquiète. Elle suit la fenêtre, se cache quand elle n'est pas
sur l'espace de travail affiché, et chaque question inclut une capture de cette fenêtre,
même si une autre a le focus. Glisse-la le long du bord pour la déplacer ; éloigne-la du
bord (ou `custos detach`) pour la détacher. Elle se détache aussi quand la fenêtre se ferme.

Une icône dans la barre (caelestia) permet de la ranger : clic pour la cacher ou la faire
réapparaître, clic droit pour lui poser une question. Aussi via le clic droit sur la mascotte
(« Se ranger dans la barre ») ou `custos toggle`. Elle reste rangée d'une session à l'autre.

Elle disparaît quand une fenêtre passe en plein écran (vidéo, jeu, présentation) et revient
ensuite ; une fenêtre simplement maximisée ne la cache pas (`hide_in_fullscreen` dans la config).

Un clic sur la mascotte (ou `custos ask`) ouvre la bulle : tape ta question, Entrée,
et la réponse s'affiche au fil de l'eau. Tu peux enchaîner avec une question de suivi ;
Échap ou ✕ ferme la bulle et termine la conversation.

Custos remarque aussi, localement et sans appeler Claude, ce que tu pourrais vouloir lui
montrer : un texte sélectionné (📋), une image copiée ou une capture `SUPER + SHIFT + S`
de caelestia (🖼️). Un badge s'allume quelques secondes sur la mascotte ; un clic ouvre la
bulle avec ce contenu. Après une commande en échec, le clic propose plutôt de l'expliquer.
Réglages dans la section `[notice]` de la config.

## Intégration au bureau (Hyprland + caelestia + fish)

```bash
mkdir -p ~/.local/bin && ln -sfn "$PWD/.venv/bin/custos" ~/.local/bin/custos
```

À la fin de `~/.config/caelestia/hypr-user.lua` (démarrage avec Hyprland et `SUPER + G`) :

```lua
dofile(os.getenv("HOME") .. "/Documents/Projets/custos/integrations/hyprland/custos.lua")
```

Dans `~/.config/caelestia/user-config.fish` (commande dans le PATH et hook des commandes en échec) :

```fish
fish_add_path -g ~/.local/bin
source ~/Documents/Projets/custos/integrations/shell/custos.fish
```

Puis `hyprctl reload`. Un hook bash existe aussi : `integrations/shell/custos.bash`.

## Structure

```
src/custos/
├── cli.py          commandes `custos daemon | start | stop | ask | attach …`
├── supervisor.py   relance du daemon s'il plante, fichier de log, start/stop/status
├── daemon.py       boucle principale
├── ipc.py          socket Unix entre les commandes et le daemon
├── claude.py       appels à `claude -p`
├── config.py       lecture de ~/.config/custos/config.toml
├── states.py       états de la mascotte
├── watch.py        surveillance de la sélection, du presse-papier et des captures
├── context/        fenêtre active, sélection, capture, filtrage des secrets
└── ui/
    ├── overlay.py  la mascotte : fenêtre layer-shell GTK4, au-dessus de tout
    ├── bubble.py   bulle de dialogue : saisie, réponse en streaming, copie
    ├── position.py position de la mascotte (glisser-déposer) et placement de la bulle
    ├── tray.py     icône dans la barre (StatusNotifierItem, D-Bus)
    ├── markdown.py Markdown des réponses → balisage Pango
    ├── animation.py  flottement, clignement, endormissement (sans GTK, testé)
    └── notify.py   notifications (réponses, et repli si l'overlay est impossible)
assets/sprites/     images de la mascotte, un dossier par état
assets/icon.png     icône de la barre
scripts/            préparation des sprites
integrations/       snippets Hyprland (Lua) et bash
tests/
```
