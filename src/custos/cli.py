"""Point d'entrée en ligne de commande : `custos <commande>`.

Le daemon tourne en continu ; les autres commandes (appelées par les raccourcis
Hyprland ou le hook shell) lui envoient simplement un message via le socket.
"""

import argparse

from custos import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="custos", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"custos {__version__}")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("daemon", help="lance la mascotte (à démarrer avec Hyprland)")

    ask = sub.add_parser("ask", help="pose une question à Custos")
    ask.add_argument("question", nargs="?", help="question ; sans argument, ouvre la bulle")
    ask.add_argument("--selection", action="store_true", help="joint le texte sélectionné")
    ask.add_argument("--screen", action="store_true", help="joint une capture de la fenêtre active")

    shell = sub.add_parser("shell-event", help="signale la fin d'une commande (hook shell)")
    shell.add_argument("--exit-code", type=int, required=True)
    shell.add_argument("--command", default="")
    shell.add_argument("--cwd", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.subcommand == "daemon":
            from custos.daemon import run

            return run()

        from custos import ipc

        if args.subcommand == "ask":
            ipc.send(
                {
                    "type": "ask",
                    "question": args.question,
                    "selection": args.selection,
                    "screen": args.screen,
                }
            )
        elif args.subcommand == "shell-event":
            ipc.send(
                {
                    "type": "shell-event",
                    "exit_code": args.exit_code,
                    "command": args.command,
                    "cwd": args.cwd,
                }
            )
    except NotImplementedError as exc:
        print(f"custos: pas encore implémenté ({exc})")
        return 1

    return 0
