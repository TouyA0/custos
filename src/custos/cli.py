"""Point d'entrée en ligne de commande : `custos <commande>`.

Le daemon tourne en continu ; les autres commandes (appelées par les raccourcis
Hyprland ou le hook shell) lui envoient simplement un message via le socket.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from custos import __version__, ipc, supervisor

MAX_OUTPUT = 100_000  # caractères : la fin de la sortie suffit, et le socket a une limite


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
    sub.add_parser("open", help="ouvre la conversation en cours en grand (Claude Code)")

    ask = sub.add_parser("ask", help="pose une question à Custos")
    ask.add_argument("question", nargs="?", help="question ; sans argument, ouvre la bulle")
    ask.add_argument("--selection", action="store_true", help="joint le texte sélectionné")
    ask.add_argument("--screen", action="store_true", help="joint une capture de la fenêtre active")

    shell = sub.add_parser("shell-event", help="signale la fin d'une commande (hook shell)")
    shell.add_argument("--exit-code", type=int, required=True)
    shell.add_argument("--command", default="")
    shell.add_argument("--cwd", default="")

    sub.add_parser(
        "shell-output",
        help="transmet la sortie de la dernière commande, lue sur l'entrée standard (terminal)",
    )

    sub.add_parser(
        "agent-event",
        help="signale l'état d'une session Claude Code (hook, JSON sur l'entrée standard)",
    )

    return parser


def ancestors(pid: int, limit: int = 64) -> list[int]:
    """`pid` et ses parents, jusqu'à init : pour retrouver la fenêtre qui les contient."""
    pids = []
    while pid > 1 and len(pids) < limit:
        pids.append(pid)
        try:
            # « pid (nom) état ppid … » : le nom peut contenir des espaces, on part de la fin.
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def agent_event(raw: str) -> dict | None:
    """Message pour le daemon à partir de l'entrée d'un hook Claude Code."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("session_id"):
        return None
    return {
        "type": "agent-event",
        "event": data.get("hook_event_name", ""),
        "session": data["session_id"],
        "cwd": data.get("cwd", ""),
        "notification": data.get("notification_type", ""),
        "message": (data.get("message") or "")[:200],
        "pids": ancestors(os.getppid()),
    }


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

        if args.subcommand in ("attach", "detach", "toggle", "open"):
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
        elif args.subcommand == "agent-event":
            # Appelé par Claude Code : jamais d'erreur, même sans daemon ou entrée illisible.
            message = agent_event(sys.stdin.read(MAX_OUTPUT))
            if message:
                try:
                    ipc.send(message)
                except ipc.DaemonNotRunning:
                    pass
        elif args.subcommand == "shell-output":
            output = sys.stdin.read()[-MAX_OUTPUT:]
            try:
                ipc.send({"type": "shell-output", "output": output})
            except ipc.DaemonNotRunning:
                pass  # appelé par le terminal, sans personne pour lire un message
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
