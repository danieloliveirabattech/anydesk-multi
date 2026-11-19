# -*- coding: utf-8 -*-
"""Gerenciador de múltiplas instâncias isoladas do AnyDesk.

Ideia central
-------------
O AnyDesk grava seu perfil (incluindo o ID da máquina, em ``system.conf``)
dentro de ``%APPDATA%\\AnyDesk``. Se cada instância for lançada com as variáveis
``USERPROFILE`` / ``APPDATA`` / ``LOCALAPPDATA`` / ``HOMEPATH`` apontando para um
diretório próprio, cada uma passa a ter **seu próprio perfil e seu próprio ID**,
rodando em paralelo e sem conflito.

Para que o AnyDesk aceite várias instâncias e para conseguir identificar cada
processo depois, o executável é copiado uma vez por slot
(``anydesk_slot1.exe``, ``anydesk_slot2.exe``, ...) dentro do diretório de dados.

O que este módulo NÃO faz (de propósito): nenhuma rede, licença, telemetria,
auto-update ou armazenamento de senhas. É só orquestração local de processos.
"""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import config

# BELOW_NORMAL_PRIORITY_CLASS — só existe/importa no Windows.
_BELOW_NORMAL_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)

NOT_STARTED = "não iniciado"


@dataclass
class SlotStatus:
    """Estado observável de um slot."""

    slot: int
    running: bool
    pids: list[int] = field(default_factory=list)
    raw_id: str = NOT_STARTED
    formatted_id: str = NOT_STARTED


class AnyDeskManager:
    """Orquestra até ``max_slots`` instâncias isoladas do AnyDesk."""

    def __init__(
        self,
        anydesk_exe: str | None = None,
        data_dir: str | None = None,
        max_slots: int = config.DEFAULT_MAX_SLOTS,
        lower_priority: bool = False,
        slot_binaries: dict[int, str] | None = None,
        sources_dir: str | None = None,
    ) -> None:
        exe = config.find_anydesk_exe(anydesk_exe)
        if exe is None:
            raise FileNotFoundError(
                "AnyDesk.exe não encontrado. Instale o AnyDesk, defina a variável "
                f"de ambiente {config.ENV_ANYDESK_EXE} ou use o argumento "
                "--exe apontando para o executável."
            )
        self.anydesk_exe: Path = exe
        self.data_dir: Path = (
            Path(data_dir).expanduser() if data_dir else config.default_data_dir()
        )
        self.max_slots = max(1, int(max_slots))
        self.lower_priority = lower_priority

        # Binário-fonte por slot (para isolamento real de serviço/IPC).
        # Prioridade: mapeamento explícito > pasta de convenção > exe único.
        self.sources_dir: Path = (
            Path(sources_dir).expanduser()
            if sources_dir
            else self.data_dir / config.SLOT_SOURCES_DIRNAME
        )
        self.slot_binaries: dict[int, Path] = {
            int(k): Path(v).expanduser() for k, v in (slot_binaries or {}).items()
        }

        self._id_cache: dict[int, str] = {}
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Resolução do binário-fonte de cada slot
    # ------------------------------------------------------------------ #
    def source_exe_for_slot(self, slot: int) -> Path:
        """Qual AnyDesk.exe usar como ORIGEM da cópia deste slot.

        Ordem: mapeamento explícito -> ``sources/AnyDesk_Inst<slot>.exe`` ->
        binário embutido por slot (``vendor/AnyDesk_Inst<slot>.exe``) ->
        o executável único (fallback, mesmo binário para todos).
        """
        explicit = self.slot_binaries.get(slot)
        if explicit and explicit.exists():
            return explicit
        conv = self.sources_dir / config.slot_source_filename(slot)
        if conv.exists():
            return conv
        bundled = config.bundled_slot_exe(slot)
        if bundled and bundled.exists():
            return bundled
        return self.anydesk_exe

    def uses_distinct_binary(self, slot: int) -> bool:
        """True se o slot tem binário-fonte próprio (não o fallback único)."""
        src = self.source_exe_for_slot(slot)
        bundled = config.bundled_slot_exe(slot)
        return (bundled is not None and src == bundled) or (src != self.anydesk_exe)

    # ------------------------------------------------------------------ #
    # Caminhos por slot
    # ------------------------------------------------------------------ #
    def _slot_dir(self, slot: int) -> Path:
        return self.data_dir / f"slot{slot}"

    def _profile_root(self, slot: int) -> Path:
        return self._slot_dir(slot) / "profile"

    def _roaming(self, slot: int) -> Path:
        return self._profile_root(slot) / "AppData" / "Roaming"

    def _local(self, slot: int) -> Path:
        return self._profile_root(slot) / "AppData" / "Local"

    def _anydesk_conf_dir(self, slot: int) -> Path:
        return self._roaming(slot) / "AnyDesk"

    def _conf_file(self, slot: int) -> Path:
        return self._anydesk_conf_dir(slot) / "system.conf"

    def _slot_exe(self, slot: int) -> Path:
        """Garante uma cópia dedicada do AnyDesk para o slot e a retorna.

        A origem é resolvida por :meth:`source_exe_for_slot` — assim cada slot
        pode usar um binário/versão diferente, o que faz o AnyDesk rodar um
        ``--local-service`` próprio por slot (encerramento independente).
        """
        source = self.source_exe_for_slot(slot)
        bin_dir = self.data_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        dst = bin_dir / f"anydesk_slot{slot}.exe"
        try:
            src_size = source.stat().st_size
            if not dst.exists() or dst.stat().st_size != src_size:
                shutil.copy2(source, dst)
        except OSError:
            # Se a cópia falhar, ainda é possível usar a origem diretamente.
            return source
        return dst

    def _make_profile_dirs(self, slot: int) -> None:
        for d in (
            self._anydesk_conf_dir(slot),
            self._local(slot) / "AnyDesk",
            self._local(slot) / "Temp",
        ):
            d.mkdir(parents=True, exist_ok=True)

    def _env_for_slot(self, slot: int) -> dict[str, str]:
        import os

        env = os.environ.copy()
        root = str(self._profile_root(slot))
        env["USERPROFILE"] = root
        env["HOMEPATH"] = root
        env["APPDATA"] = str(self._roaming(slot))
        env["LOCALAPPDATA"] = str(self._local(slot))
        return env

    # ------------------------------------------------------------------ #
    # ID do AnyDesk
    # ------------------------------------------------------------------ #
    def get_id(self, slot: int, force_reload: bool = False) -> str:
        """Lê ``ad.anynet.id`` do ``system.conf`` do slot (com cache)."""
        if not force_reload:
            cached = self._id_cache.get(slot)
            if cached and cached != NOT_STARTED:
                return cached

        conf = self._conf_file(slot)
        try:
            if conf.exists():
                for line in conf.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("ad.anynet.id="):
                        raw = line.split("=", 1)[1].strip()
                        if raw:
                            self._id_cache[slot] = raw
                            return raw
        except OSError:
            pass

        self._id_cache[slot] = NOT_STARTED
        return NOT_STARTED

    @staticmethod
    def format_id(raw_id: str) -> str:
        """Formata o ID no padrão do AnyDesk (ex.: ``141 670 174``)."""
        if not raw_id or not raw_id.isdigit():
            return raw_id
        if len(raw_id) == 9:
            return f"{raw_id[0:3]} {raw_id[3:6]} {raw_id[6:9]}"
        if len(raw_id) == 10:
            return f"{raw_id[0:1]} {raw_id[1:4]} {raw_id[4:7]} {raw_id[7:10]}"
        return raw_id

    # ------------------------------------------------------------------ #
    # Varredura de processos
    # ------------------------------------------------------------------ #
    def scan(self) -> dict[int, SlotStatus]:
        """Mapeia os processos AnyDesk em execução para seus slots."""
        pids_by_slot: dict[int, list[int]] = {
            s: [] for s in range(1, self.max_slots + 1)
        }

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info["name"] or "").lower()
                if "anydesk" not in name:
                    continue
                try:
                    cmd = " ".join(proc.cmdline() or []).lower()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    cmd = ""
                for slot in range(1, self.max_slots + 1):
                    tag_slot = f"anydesk_slot{slot}"
                    tag_inst = f"anydesk_inst{slot}"
                    if tag_slot in name or tag_slot in cmd or tag_inst in name or tag_inst in cmd:
                        pids_by_slot[slot].append(proc.info["pid"])
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        status: dict[int, SlotStatus] = {}
        for slot in range(1, self.max_slots + 1):
            pids = pids_by_slot[slot]
            raw = self.get_id(slot)
            status[slot] = SlotStatus(
                slot=slot,
                running=bool(pids),
                pids=pids,
                raw_id=raw,
                formatted_id=self.format_id(raw),
            )
        return status

    def is_running(self, slot: int) -> bool:
        return self.scan().get(slot, SlotStatus(slot, False)).running

    # ------------------------------------------------------------------ #
    # Sanitização preventiva
    # ------------------------------------------------------------------ #
    def sanitize_slot(self, slot: int) -> None:
        """Limpeza preventiva antes do launch para evitar colisões e sessões presas.

        - Remove locks e traces temporários
        - Remove user.conf (elimina estado preso de sessões/abas anteriores)
        - Preserva system.conf para manter o AnyDesk ID do slot
        """
        conf_dir = self._anydesk_conf_dir(slot)
        if conf_dir.exists():
            for sub in ("cache", "global_cache", "thumbnails"):
                shutil.rmtree(conf_dir / sub, ignore_errors=True)
            for fname in (
                "system.conf.lock",
                "service.conf.lock",
                "user.conf.lock",
                "connection_trace.txt",
                "file_transfer_trace.txt",
                "ad.trace",
                "anydesk.trace",
                "user.conf",
            ):
                try:
                    (conf_dir / fname).unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------ #
    # Identificação visual de janela
    # ------------------------------------------------------------------ #
    def _tag_window_title_async(self, slot: int, remote_id: str | None = None) -> None:
        """Renomeia a janela do AnyDesk para identificar o slot (ex.: AnyDesk [SLOT 1])."""
        def _worker():
            try:
                user32 = getattr(ctypes, "windll", None)
                if not user32 or not hasattr(user32, "user32"):
                    return
                u32 = user32.user32
                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                fmt_id = self.format_id(remote_id) if remote_id else ""
                custom_title = (
                    f"AnyDesk [SESSÃO {slot}] • Conectando em {fmt_id}"
                    if fmt_id
                    else f"AnyDesk [SESSÃO {slot}]"
                )

                for delay in (1.0, 2.0, 3.5, 5.0):
                    time.sleep(delay)
                    scan = self.scan()
                    pids = set(scan.get(slot, SlotStatus(slot, False)).pids)
                    if not pids:
                        continue

                    def enum_cb(hwnd, _):
                        if u32.IsWindow(hwnd) and u32.IsWindowVisible(hwnd):
                            pid = ctypes.c_ulong()
                            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            if pid.value in pids:
                                length = u32.GetWindowTextLengthW(hwnd)
                                if length > 0:
                                    buff = ctypes.create_unicode_buffer(length + 1)
                                    u32.GetWindowTextW(hwnd, buff, length + 1)
                                    title = buff.value
                                    if "anydesk" in title.lower() and f"[sessão {slot}]".lower() not in title.lower() and f"[slot {slot}]".lower() not in title.lower():
                                        u32.SetWindowTextW(hwnd, custom_title)
                        return True

                    u32.EnumWindows(WNDENUMPROC(enum_cb), 0)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Lançamento / conexão
    # ------------------------------------------------------------------ #
    def launch(self, slot: int, remote_id: str | None = None) -> tuple[bool, str]:
        """Inicia a sessão (isolada). Se ``remote_id`` for dado, já conecta nele."""
        if not 1 <= slot <= self.max_slots:
            return False, f"Sessão {slot} fora do intervalo 1..{self.max_slots}."

        clean_id = _digits(remote_id) if remote_id else None
        if remote_id is not None and (clean_id is None or len(clean_id) < 9):
            return False, f"ID AnyDesk inválido: {remote_id!r} (mínimo 9 dígitos)."

        self._make_profile_dirs(slot)
        self.sanitize_slot(slot)
        exe = self._slot_exe(slot)
        args = [str(exe)] + ([clean_id] if clean_id else [])
        creationflags = _BELOW_NORMAL_PRIORITY if self.lower_priority else 0

        try:
            subprocess.Popen(
                args,
                env=self._env_for_slot(slot),
                cwd=str(exe.parent),
                creationflags=creationflags,
            )
        except OSError as exc:
            return False, f"Falha ao iniciar a sessão {slot}: {exc}"

        self._tag_window_title_async(slot, clean_id)
        suffix = f" conectando em {self.format_id(clean_id)}" if clean_id else ""
        return True, f"Sessão {slot} iniciada{suffix}."

    def connect(self, remote_id: str) -> tuple[bool, str, int]:
        """Conecta a ``remote_id`` na primeira sessão livre.

        Retorna ``(sucesso, mensagem, sessao_usada)``. Se nenhuma sessão estiver
        livre, ``sessao_usada`` é ``0``.
        """
        clean_id = _digits(remote_id)
        if not clean_id or len(clean_id) < 9:
            return False, f"ID AnyDesk inválido: {remote_id!r} (mínimo 9 dígitos).", 0

        scan = self.scan()
        free = [s for s in range(1, self.max_slots + 1) if not scan[s].running]
        if not free:
            return (
                False,
                f"Todas as {self.max_slots} sessões estão ocupadas. "
                "Encerre uma conexão antes de abrir outra.",
                0,
            )

        slot = free[0]
        ok, msg = self.launch(slot, remote_id=clean_id)
        return ok, msg, slot

    # ------------------------------------------------------------------ #
    # Encerramento
    # ------------------------------------------------------------------ #
    def kill(self, slot: int) -> int:
        """Encerra os processos do slot. Retorna quantos foram encerrados."""
        count = 0
        for pid in self.scan().get(slot, SlotStatus(slot, False)).pids:
            try:
                psutil.Process(pid).kill()
                count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if count:
            time.sleep(0.2)
        return count

    def kill_all(self) -> int:
        return sum(self.kill(s) for s in range(1, self.max_slots + 1))

    # ------------------------------------------------------------------ #
    # Limpeza de perfil / novo ID
    # ------------------------------------------------------------------ #
    def clean(self, slot: int, new_id: bool = False, wait: float = 6.0) -> tuple[bool, list[str]]:
        """Encerra o slot e limpa seu cache de sessão.

        Com ``new_id=True`` o perfil inteiro é apagado e o AnyDesk é subido
        rapidamente só para gerar um ID novo, depois encerrado.
        Retorna ``(sucesso, [ações executadas])``.
        """
        self.kill(slot)
        time.sleep(0.3)
        self._id_cache.pop(slot, None)
        actions: list[str] = []

        profile = self._profile_root(slot)
        if new_id:
            if profile.exists():
                shutil.rmtree(profile, ignore_errors=True)
                actions.append("perfil do slot apagado")
            self._make_profile_dirs(slot)
            actions.append(self._regenerate_id(slot, wait))
            return True, actions

        # Limpeza leve: só caches e locks; preserva system.conf (mantém o ID).
        conf_dir = self._anydesk_conf_dir(slot)
        if conf_dir.exists():
            for sub in ("cache", "global_cache", "thumbnails", "chat", "recordings"):
                shutil.rmtree(conf_dir / sub, ignore_errors=True)
            for fname in (
                "system.conf.lock",
                "service.conf.lock",
                "user.conf.lock",
                "connection_trace.txt",
                "file_transfer_trace.txt",
                "ad.trace",
                "anydesk.trace",
                "user.conf",
            ):
                try:
                    (conf_dir / fname).unlink()
                except OSError:
                    pass
            actions.append("cache e traces limpos (ID preservado)")
        return True, actions

    def _regenerate_id(self, slot: int, wait: float) -> str:
        """Sobe o AnyDesk o suficiente para ele gravar um ID novo, depois encerra."""
        exe = self._slot_exe(slot)
        conf = self._conf_file(slot)
        try:
            proc = subprocess.Popen(
                [str(exe)], env=self._env_for_slot(slot), cwd=str(exe.parent)
            )
        except OSError as exc:
            return f"falha ao gerar novo ID: {exc}"

        deadline = time.time() + max(1.0, wait)
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                if conf.exists() and conf.stat().st_size > 0:
                    if any(
                        l.startswith("ad.anynet.id=")
                        for l in conf.read_text(errors="ignore").splitlines()
                    ):
                        break
            except OSError:
                continue

        try:
            proc.terminate()
            time.sleep(0.3)
            proc.kill()
        except OSError:
            pass
        self.kill(slot)

        new = self.get_id(slot, force_reload=True)
        return f"novo ID gerado: {self.format_id(new)}"


def _digits(value: str | None) -> str | None:
    if value is None:
        return None
    return "".join(ch for ch in str(value) if ch.isdigit()) or None
