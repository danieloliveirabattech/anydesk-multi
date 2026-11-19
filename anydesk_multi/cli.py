# -*- coding: utf-8 -*-
"""Interface de linha de comando do anydesk-multi."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .manager import AnyDeskManager


def _build_manager(args: argparse.Namespace) -> AnyDeskManager:
    return AnyDeskManager(
        anydesk_exe=args.exe,
        data_dir=args.data_dir,
        max_slots=args.slots,
        lower_priority=args.low_priority,
        sources_dir=args.sources_dir,
    )


def _cmd_list(mgr: AnyDeskManager, _args: argparse.Namespace) -> int:
    scan = mgr.scan()
    print(f"{'SESSÃO':<8}{'ESTADO':<12}{'ID ANYDESK':<16}{'PIDS'}")
    print("-" * 45)
    for slot in sorted(scan):
        st = scan[slot]
        estado = "em execução" if st.running else "parado"
        pids = ",".join(map(str, st.pids)) or "-"
        print(f"{slot:<8}{estado:<12}{st.formatted_id:<16}{pids}")
    return 0


def _cmd_ids(mgr: AnyDeskManager, _args: argparse.Namespace) -> int:
    for slot in range(1, mgr.max_slots + 1):
        print(f"sessão {slot}: {mgr.format_id(mgr.get_id(slot, force_reload=True))}")
    return 0


def _cmd_connect(mgr: AnyDeskManager, args: argparse.Namespace) -> int:
    ok, msg, slot = mgr.connect(args.id)
    print(msg if not ok else f"[sessão {slot}] {msg}")
    return 0 if ok else 1


def _cmd_launch(mgr: AnyDeskManager, args: argparse.Namespace) -> int:
    ok, msg = mgr.launch(args.slot, remote_id=args.id)
    print(msg)
    return 0 if ok else 1


def _cmd_kill(mgr: AnyDeskManager, args: argparse.Namespace) -> int:
    n = mgr.kill(args.slot)
    print(f"sessão {args.slot}: {n} processo(s) encerrado(s).")
    return 0


def _cmd_kill_all(mgr: AnyDeskManager, _args: argparse.Namespace) -> int:
    print(f"{mgr.kill_all()} processo(s) encerrado(s) no total.")
    return 0


def _cmd_clean(mgr: AnyDeskManager, args: argparse.Namespace) -> int:
    ok, actions = mgr.clean(args.slot, new_id=args.new_id)
    print(f"sessão {args.slot}:")
    for a in actions:
        print(f"  - {a}")
    return 0 if ok else 1


def _cmd_info(mgr: AnyDeskManager, _args: argparse.Namespace) -> int:
    print(f"AnyDesk.exe    : {mgr.anydesk_exe}")
    print(f"Dados          : {mgr.data_dir}")
    print(f"Fontes/sessão  : {mgr.sources_dir}")
    print(f"Sessões        : {mgr.max_slots}")
    distinct = sum(mgr.uses_distinct_binary(s) for s in range(1, mgr.max_slots + 1))
    print(f"Binário/sessão : {distinct}/{mgr.max_slots} com binário próprio")
    return 0


def _cmd_sources(mgr: AnyDeskManager, _args: argparse.Namespace) -> int:
    from .config import slot_source_filename

    print(f"Pasta de fontes: {mgr.sources_dir}")
    print("Coloque um AnyDesk.exe (de preferência versões diferentes) como:")
    for slot in range(1, mgr.max_slots + 1):
        src = mgr.source_exe_for_slot(slot)
        own = mgr.uses_distinct_binary(slot)
        marca = "próprio" if own else "fallback (exe único)"
        print(
            f"  sessão {slot}: {mgr.sources_dir / slot_source_filename(slot)}"
            f"  -> {marca}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="anydesk-multi",
        description="Roda várias instâncias isoladas do AnyDesk em paralelo.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--exe", help="Caminho para o AnyDesk.exe (sobrepõe a detecção).")
    p.add_argument("--data-dir", help="Diretório de dados/perfis (padrão: automático).")
    p.add_argument(
        "--sources-dir",
        help="Pasta com um AnyDesk.exe por sessão (padrão: <data-dir>/sources).",
    )
    p.add_argument(
        "--slots", type=int, default=None,
        help="Número de sessões gerenciadas (padrão: 4).",
    )
    p.add_argument(
        "--low-priority", action="store_true",
        help="Inicia o AnyDesk com prioridade abaixo do normal.",
    )

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="Mostra caminhos e configuração resolvidos.")
    sub.add_parser("sources", help="Mostra o binário-fonte de cada sessão.")
    sub.add_parser("list", help="Lista o estado de cada sessão.")
    sub.add_parser("ids", help="Mostra o ID AnyDesk de cada sessão.")

    c = sub.add_parser("connect", help="Conecta a um ID na primeira sessão livre.")
    c.add_argument("id", help="ID AnyDesk do destino (>= 9 dígitos).")

    l = sub.add_parser("launch", help="Inicia uma sessão específica.")
    l.add_argument("slot", type=int, help="Número da sessão (ex.: 1, 2, ...)")
    l.add_argument("id", nargs="?", default=None, help="ID opcional para já conectar.")

    k = sub.add_parser("kill", help="Encerra uma sessão específica.")
    k.add_argument("slot", type=int, help="Número da sessão (ex.: 1, 2, ...)")

    sub.add_parser("kill-all", help="Encerra todas as sessões gerenciadas.")

    cl = sub.add_parser("clean", help="Limpa o cache de uma sessão.")
    cl.add_argument("slot", type=int, help="Número da sessão (ex.: 1, 2, ...)")
    cl.add_argument(
        "--new-id", action="store_true",
        help="Apaga o perfil e gera um AnyDesk ID novo.",
    )
    return p


_COMMANDS = {
    "info": _cmd_info,
    "sources": _cmd_sources,
    "list": _cmd_list,
    "ids": _cmd_ids,
    "connect": _cmd_connect,
    "launch": _cmd_launch,
    "kill": _cmd_kill,
    "kill-all": _cmd_kill_all,
    "clean": _cmd_clean,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # --slots default aplicado aqui para não fixar o número no parser.
    if args.slots is None:
        from .config import DEFAULT_MAX_SLOTS

        args.slots = DEFAULT_MAX_SLOTS

    try:
        mgr = _build_manager(args)
    except FileNotFoundError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2

    return _COMMANDS[args.command](mgr, args)


if __name__ == "__main__":
    raise SystemExit(main())
