# Custos : hook fish, à sourcer depuis ~/.config/caelestia/user-config.fish (ou config.fish).
# Signale au daemon chaque commande qui échoue (la réaction reste locale).

function __custos_postexec --on-event fish_postexec
    set -l exit_code $status
    # 130 et 148 : Ctrl+C et Ctrl+Z, pas des erreurs.
    contains -- $exit_code 0 130 148; and return
    test -n "$argv[1]"; or return
    command -q custos; or return
    command custos shell-event --exit-code $exit_code --command "$argv[1]" --cwd "$PWD" >/dev/null 2>&1 &
    disown 2>/dev/null  # pas de message « Job terminé » dans le terminal
end
