"""Point d'entrée en ligne de commande : `custos <commande>`.

Le daemon tourne en continu ; les autres commandes (appelées par les raccourcis
Hyprland ou le hook shell) lui envoient simplement un message via le socket.
"""

import argparse

from custos import __version__, ipc, supervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="custos", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"custos {__version__}")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    daemon = sub.add_parser("daemon", help="lance la mascotte et la relance si elle plante")
    daemon.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("start", help="démarre Custos en arrière-plan")
    sub.add_parser("stop", help="arrête Custos")
    sub.add_parser("restart", help="redémarre Custos (après une modification du code)")
    sub.add_parser("status", help="indique si Custos tourne")
    logs = sub.add_parser("logs", help="affiche le log")
    logs.add_argument("-f", "--follow", action="store_true", help="suit le log en direct")
    logs.add_argument("-n", "--lines", type=int, default=50)

    sub.add_parser("attach", help="accroche la mascotte à la fenêtre active")
    sub.add_parser("detach", help="détache la mascotte de sa fenêtre")
    sub.add_parser("toggle", help="range la mascotte dans la barre, ou la fait réapparaître")

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
            if args.worker:
                from custos.daemon import run

                return run()
            return supervisor.supervise()
        if args.subcommand in ("start", "stop", "restart", "status"):
            return getattr(supervisor, args.subcommand)()
        if args.subcommand == "logs":
            return supervisor.logs(args.follow, args.lines)

        if args.subcommand in ("attach", "detach", "toggle"):
            ipc.send({"type": args.subcommand})
        elif args.subcommand == "ask":
            ipc.send(
                {
                    "type": "ask",
                    "question": args.question,
                    "selection": args.selection,
                    "screen": args.screen,
                }
            )
        elif args.subcommand == "shell-event":
            try:
                ipc.send(
                    {
                        "type": "shell-event",
                        "exit_code": args.exit_code,
                        "command": args.command,
                        "cwd": args.cwd,
                    }
                )
            except ipc.DaemonNotRunning:
                pass  # appelé après chaque commande : silencieux si Custos ne tourne pas
    except ipc.DaemonNotRunning:
        # `custos ask` vient souvent d'un raccourci, sans terminal : on prévient aussi à l'écran.
        from custos.ui.notify import notify

        message = "Custos ne tourne pas : lance `custos start`."
        print(f"custos: {message}")
        notify("Custos", message)
        return 1
    except NotImplementedError as exc:
        print(f"custos: pas encore implémenté ({exc})")
        return 1

    return 0
