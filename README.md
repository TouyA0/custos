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
- Custos **agit** sur ton PC avec les outils de Claude Code, en mode auto : il lit,
  lance des commandes, modifie ta config, ouvre une page… et ne te demande ton accord
  que pour ce que le mode auto juge sensible. Jamais de `sudo` ni de lecture de tes
  secrets (`tools = false` dans `[claude]` pour qu'il réponde seulement).
- Certaines fenêtres ne sont jamais lues : une appli entière (`blocked_classes`, ton
  gestionnaire de mots de passe) ou une page précise d'après son titre (`blocked_titles`,
  ta banque ou la navigation privée dans le navigateur).

## Prérequis (Arch Linux)

```bash
sudo pacman -S python python-gobject gtk4 gtk4-layer-shell grim wl-clipboard
```

Et [Claude Code](https://claude.com/claude-code), connecté à ton compte (`claude /login`).

## Installation (développement)

```bash
python -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
mkdir -p ~/.config/custos && cp config/config.example.toml ~/.config/custos/config.toml
cp config/custos.example.md ~/.config/custos/custos.md
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

Pour l'**accrocher à une fenêtre**, glisse-la sur le bord haut de cette fenêtre : elle prend
la pose assise quand elle survole un bord, et s'y assoit quand tu la lâches. Autre moyen :
clic droit sur la mascotte → **S'accrocher** à la fenêtre où tu étais (ou `custos attach`),
et elle s'assoit près du coin haut-gauche. Elle se lève quand
elle réfléchit, parle ou s'inquiète. Elle suit la fenêtre, se cache quand elle n'est pas
sur l'espace de travail affiché, et chaque question inclut une capture de cette fenêtre,
même si une autre a le focus. Glisse-la le long du bord pour la déplacer, sur le bord d'une
autre fenêtre pour changer, ou lâche-la ailleurs (ou `custos detach`) pour la détacher. Elle se détache aussi quand la fenêtre se ferme.

Une icône dans la barre (caelestia) permet de la ranger : clic pour la cacher ou la faire
réapparaître, clic droit pour lui poser une question. Aussi via le clic droit sur la mascotte
(« Se ranger dans la barre ») ou `custos toggle`. Elle reste rangée d'une session à l'autre.

Avec plusieurs écrans, glisse-la d'un écran à l'autre : elle retient celui où tu l'as posée,
y retourne quand tu le rebranches, et passe sur l'écran actif s'il est débranché. Accrochée,
elle suit sa fenêtre d'un écran à l'autre.

Elle disparaît quand une fenêtre passe en plein écran (vidéo, jeu, présentation) et revient
ensuite ; une fenêtre simplement maximisée ne la cache pas (`hide_in_fullscreen` dans la config).

Un clic sur la mascotte (ou `custos ask`) ouvre une petite carte : tape ta question,
Entrée, et seule la réponse s'affiche, au fil de l'eau. « Répondre… » pose une question
de suivi (la réponse affichée est remplacée, Claude garde le fil). Rouvrir la carte dans
les 10 minutes reprend la conversation ; ＋ en démarre une nouvelle. ↗ (ou `custos open`)
ouvre la conversation complète en grand : Claude Code dans un terminal, avec
`claude --resume`. Un clic en dehors de la carte, Échap ou ✕ la ferment.

Dans la carte, tape `/` pour les commandes : `/h` (ou `/historique`) liste tes dernières
conversations, `/h caté` les filtre, ↑ ↓ et Entrée en rouvrent une, prête pour la suite ;
`/n` en commence une nouvelle (comme ＋) et `/g` ouvre la conversation en grand (comme ↗).
Une question qui commence par un chemin (`/etc/fstab c'est quoi ?`) reste une question.

Quand il agit (« mon PC a crashé, trouve et règle le problème », « ouvre-moi le
Catéchisme sur les anges »), la carte montre l'étape en cours (`▶ journalctl -b -1`), puis
seulement sa conclusion ; le détail de ce qu'il a fait est dans la conversation en grand (↗).
Une action sensible attend ton accord dans la carte : **Autoriser**, **Toujours** (pour cette
conversation) ou **Refuser**. Tu peux fermer la carte pendant qu'il travaille : il continue,
la mascotte affiche ✋ s'il a besoin de toi et ✅ quand il a fini, et un clic rouvre la carte
où il en est. ■ l'arrête. Pour une commande en root, il t'ouvre un terminal avec la commande
prête (`custos terminal '…'`) : c'est toi qui la valides et tapes ton mot de passe.

Custos détecte ta machine (distribution, Hyprland, terminal, shell, caelestia, yay…) pour
te donner des commandes qui s'y appliquent. Son caractère, et ce qu'il doit savoir de toi,
c'est toi qui l'écris dans `~/.config/custos/custos.md` (voir `config/custos.example.md`) :
le fichier est joint à chaque question, sans redémarrer.

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

**Tes sessions Claude Code** : avec les hooks de `integrations/claude-code/settings.json`
ajoutés à `~/.claude/settings.json`, la mascotte affiche ✋ quand une session attend ta
permission et ✅ quand elle a fini. Un clic t'emmène dans sa fenêtre (terminal, VS Code,
appli Claude…). ✋ reste tant qu'elle attend, ✅ jusqu'à ce que tu ailles voir. Tout est local :
Custos ne lit que l'état de la session, jamais son contenu (`[agents]` dans la config).

Pour que Claude voie aussi la **sortie d'une commande en échec** (le message d'erreur, pas
seulement la commande), ajoute le raccourci de `integrations/terminal/foot.ini` à
`~/.config/foot/foot.ini` et `capture_output = true` dans la section `[shell]` de la config.
Après un échec, Custos demande la sortie à foot (via Hyprland, sans prendre le focus) et la
garde en mémoire, sans appeler Claude : elle n'est jointe, secrets masqués, que si tu
cliques pour demander de l'aide.

## Structure

```
src/custos/
├── cli.py          commandes `custos daemon | start | stop | ask | attach …`
├── supervisor.py   relance du daemon s'il plante, fichier de log, start/stop/status
├── daemon.py       boucle principale
├── ipc.py          socket Unix entre les commandes et le daemon
├── claude.py       appels à `claude -p`
├── history.py      historique des conversations (fichiers de Claude Code)
├── config.py       lecture de ~/.config/custos/config.toml et custos.md
├── machine.py      détection de la machine (distribution, terminal, shell…)
├── states.py       états de la mascotte
├── watch.py        surveillance de la sélection, du presse-papier et des captures
├── context/        fenêtre active, sélection, capture, filtrage des secrets
└── ui/
    ├── overlay.py  la mascotte : fenêtre layer-shell GTK4, au-dessus de tout
    ├── bubble.py   bulle de dialogue : saisie, réponse en streaming, copie
    ├── position.py position de la mascotte (glisser-déposer) et placement de la bulle
    ├── tray.py     icône dans la barre (StatusNotifierItem, D-Bus)
    ├── slash.py    commandes « / » de la carte (/h, /n, /g)
    ├── markdown.py Markdown des réponses → balisage Pango
    ├── animation.py  flottement, clignement, endormissement (sans GTK, testé)
    └── notify.py   notifications (réponses, et repli si l'overlay est impossible)
assets/sprites/     images de la mascotte, un dossier par état
assets/icon.png     icône de la barre
scripts/            préparation des sprites
integrations/       snippets Hyprland (Lua), shell (fish, bash), terminal (foot), Claude Code
tests/
```
