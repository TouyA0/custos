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

Intégrations : `integrations/hyprland/custos.lua` (démarrage et raccourcis) et
`integrations/shell/custos.bash` (hook bash).

## Structure

```
src/custos/
├── cli.py          commandes `custos daemon | ask | shell-event`
├── daemon.py       boucle principale
├── ipc.py          socket Unix entre les commandes et le daemon
├── claude.py       appels à `claude -p`
├── config.py       lecture de ~/.config/custos/config.toml
├── states.py       états de la mascotte
├── context/        fenêtre active, sélection, capture, filtrage des secrets
└── ui/             overlay de la mascotte et bulle de dialogue
assets/sprites/     animations, un dossier par état
integrations/       snippets Hyprland (Lua) et bash
tests/
```
