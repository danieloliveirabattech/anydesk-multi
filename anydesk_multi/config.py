# -*- coding: utf-8 -*-
"""Configuração: descoberta do executável do AnyDesk e caminhos de dados.

Nenhum acesso a rede, registro do Windows ou telemetria acontece aqui.
Tudo é local e previsível.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: Número padrão de instâncias (slots) gerenciadas em paralelo.
DEFAULT_MAX_SLOTS = 4

#: Variável de ambiente opcional para apontar o executável do AnyDesk.
ENV_ANYDESK_EXE = "ANYDESK_MULTI_EXE"

#: Variável de ambiente opcional para apontar o diretório de dados.
ENV_DATA_DIR = "ANYDESK_MULTI_HOME"

#: Subpasta (dentro do diretório de dados) onde o usuário pode colocar um
#: AnyDesk.exe distinto por slot, para isolamento real de serviço/IPC.
SLOT_SOURCES_DIRNAME = "sources"


def slot_source_filename(slot: int) -> str:
    """Nome de arquivo convencionado do binário-fonte de um slot."""
    return f"AnyDesk_Inst{slot}.exe"


def default_data_dir() -> Path:
    """Diretório onde ficam os perfis isolados e os binários por slot.

    Windows:  %LOCALAPPDATA%\\AnyDeskMulti
    Outros:   ~/.anydesk-multi
    """
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()

    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "AnyDeskMulti"
    return Path.home() / ".anydesk-multi"


def _candidate_exe_paths() -> list[Path]:
    """Locais comuns de instalação do AnyDesk no Windows."""
    candidates: list[str] = []

    env = os.environ.get(ENV_ANYDESK_EXE)
    if env:
        candidates.append(env)

    for base in (
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        os.environ.get("APPDATA"),  # instalação "portable" grava aqui
    ):
        if base:
            candidates.append(os.path.join(base, "AnyDesk", "AnyDesk.exe"))

    # Alguns instaladores deixam o binário versionado no perfil do usuário.
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, "AnyDesk.exe"))

    # Versão "portátil" baixada do site costuma ficar em Downloads/Desktop.
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        candidates.append(os.path.join(userprofile, "Downloads", "AnyDesk.exe"))
        candidates.append(os.path.join(userprofile, "Desktop", "AnyDesk.exe"))

    return [Path(c) for c in candidates]


def bundled_anydesk_exe() -> Path | None:
    """Caminho do AnyDesk.exe embutido no pacote (``anydesk_multi/vendor``).

    Retorna ``None`` se o pacote foi instalado sem o binário.
    """
    # Executável empacotado com PyInstaller (onefile): recursos ficam em _MEIPASS.
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        p = base / "anydesk_multi" / "vendor" / "AnyDesk.exe"
        if p.exists():
            return p

    try:
        from importlib.resources import files

        res = Path(str(files("anydesk_multi") / "vendor" / "AnyDesk.exe"))
        if res.exists():
            return res
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        pass
    fallback = Path(__file__).resolve().parent / "vendor" / "AnyDesk.exe"
    return fallback if fallback.exists() else None


def bundled_icon() -> Path | None:
    """Caminho do ícone do app (``anydesk_multi/assets/icon.ico``)."""
    rel = ("assets", "icon.ico")
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        p = base.joinpath("anydesk_multi", *rel)
        if p.exists():
            return p
    try:
        from importlib.resources import files

        res = Path(str(files("anydesk_multi").joinpath(*rel)))
        if res.exists():
            return res
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        pass
    fallback = Path(__file__).resolve().parent.joinpath(*rel)
    return fallback if fallback.exists() else None


def find_anydesk_exe(explicit: str | None = None) -> Path | None:
    """Resolve o caminho do AnyDesk.exe.

    Ordem: argumento explícito -> variável de ambiente -> binário EMBUTIDO no
    pacote -> instalação do sistema -> PATH. Retorna ``None`` se nada servir.
    """
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None

    env = os.environ.get(ENV_ANYDESK_EXE)
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p

    # Binário embutido: é o padrão do projeto.
    bundled = bundled_anydesk_exe()
    if bundled is not None:
        return bundled

    for p in _candidate_exe_paths():
        if p.exists():
            return p

    found = shutil.which("AnyDesk") or shutil.which("anydesk")
    return Path(found) if found else None
