# Custos : hook bash, à sourcer depuis ~/.bashrc (c'est aussi le shell du terminal de VS Code).
# Signale au daemon chaque commande qui échoue (la réaction reste locale).

# ~/.local/bin n'est pas forcément dans le PATH de bash (fish l'y ajoute, pas bash).
__custos_bin=$(command -v custos 2>/dev/null || echo "$HOME/.local/bin/custos")

__custos_precmd() {
    local exit_code=$?
    # 130 et 148 : Ctrl+C et Ctrl+Z, pas des erreurs.
    if [[ $exit_code -ne 0 && $exit_code -ne 130 && $exit_code -ne 148 && -x $__custos_bin ]]; then
        local last_command
        last_command=$(HISTTIMEFORMAT= history 1 | sed 's/^ *[0-9]* *//')
        ("$__custos_bin" shell-event --exit-code "$exit_code" --command "$last_command" --cwd "$PWD" \
            >/dev/null 2>&1 &)
    fi
    return $exit_code
}

PROMPT_COMMAND="__custos_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
