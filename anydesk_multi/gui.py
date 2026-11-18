# -*- coding: utf-8 -*-
"""Painel gráfico (PyQt6) para gerenciar os slots do AnyDesk.

Inspirado na janela principal do projeto original: uma grade de cards de slot
e uma barra de "Conexão Rápida". Toda a lógica vem de :class:`AnyDeskManager`;
aqui é só apresentação. Sem nuvem, sem telemetria, sem senhas.

Execute com:  python -m anydesk_multi.gui
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "PyQt6 não está instalado. Instale com:  pip install PyQt6\n"
        "ou:  pip install -e .[gui]\n"
    )
    raise SystemExit(2)

from . import __version__, config
from .manager import NOT_STARTED, AnyDeskManager

REFRESH_MS = 2500


# --------------------------------------------------------------------------- #
# Worker genérico: roda uma função pesada fora da thread de UI
# --------------------------------------------------------------------------- #
class Worker(QtCore.QThread):
    done = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:  # noqa: BLE001 - reportado na UI
            self.failed.emit(str(exc))


# --------------------------------------------------------------------------- #
# Card de um slot
# --------------------------------------------------------------------------- #
class SlotCard(QtWidgets.QFrame):
    connect_requested = QtCore.pyqtSignal(int)   # slot
    launch_requested = QtCore.pyqtSignal(int)
    kill_requested = QtCore.pyqtSignal(int)
    clean_requested = QtCore.pyqtSignal(int, bool)  # slot, new_id

    def __init__(self, slot: int):
        super().__init__()
        self.slot = slot
        self.setObjectName("card")
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)

        title = QtWidgets.QLabel(f"Slot {slot}")
        title.setObjectName("cardTitle")

        self.dot = QtWidgets.QLabel("●")
        self.dot.setObjectName("dotOff")
        self.state = QtWidgets.QLabel("parado")
        self.state.setObjectName("state")

        head = QtWidgets.QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.dot)
        head.addWidget(self.state)

        self.id_label = QtWidgets.QLabel(NOT_STARTED)
        self.id_label.setObjectName("idLabel")
        self.id_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.pids_label = QtWidgets.QLabel("")
        self.pids_label.setObjectName("pids")

        self.btn_launch = QtWidgets.QPushButton("Abrir")
        self.btn_connect = QtWidgets.QPushButton("Conectar…")
        self.btn_kill = QtWidgets.QPushButton("Encerrar")
        self.btn_clean = QtWidgets.QPushButton("Limpar")
        self.btn_newid = QtWidgets.QPushButton("Novo ID")
        self.btn_kill.setObjectName("danger")

        self.btn_launch.clicked.connect(lambda: self.launch_requested.emit(self.slot))
        self.btn_connect.clicked.connect(lambda: self.connect_requested.emit(self.slot))
        self.btn_kill.clicked.connect(lambda: self.kill_requested.emit(self.slot))
        self.btn_clean.clicked.connect(lambda: self.clean_requested.emit(self.slot, False))
        self.btn_newid.clicked.connect(lambda: self.clean_requested.emit(self.slot, True))

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(self.btn_launch)
        row1.addWidget(self.btn_connect)
        row1.addWidget(self.btn_kill)
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(self.btn_clean)
        row2.addWidget(self.btn_newid)
        row2.addStretch(1)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.addLayout(head)
        lay.addWidget(self.id_label)
        lay.addWidget(self.pids_label)
        lay.addStretch(1)
        lay.addLayout(row1)
        lay.addLayout(row2)

    def update_status(self, st) -> None:
        running = st.running
        self.dot.setObjectName("dotOn" if running else "dotOff")
        self.state.setText("em execução" if running else "parado")
        self.id_label.setText(st.formatted_id)
        self.pids_label.setText(
            f"PIDs: {', '.join(map(str, st.pids))}" if st.pids else "—"
        )
        self.btn_kill.setEnabled(running)
        # Reaplica o estilo do dot (objectName mudou).
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)

    def set_busy(self, busy: bool) -> None:
        for b in (
            self.btn_launch, self.btn_connect, self.btn_kill,
            self.btn_clean, self.btn_newid,
        ):
            b.setEnabled(not busy)


# --------------------------------------------------------------------------- #
# Tour guiado (onboarding) — overlay escurecido + balão de dica
# --------------------------------------------------------------------------- #
class CoachOverlay(QtWidgets.QWidget):
    """Sobreposição que escurece a janela, destaca um elemento e mostra uma dica.

    Cada passo é um dict: ``{"title", "text", "targets": [widget, ...] | None}``.
    Com ``targets=None`` o passo é centralizado (sem destaque).
    """

    PAD = 8  # folga ao redor do elemento destacado

    def __init__(self, parent: QtWidgets.QWidget, steps: list[dict], on_finish):
        super().__init__(parent)
        self._steps = steps
        self._i = 0
        self._on_finish = on_finish
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())

        self.bubble = QtWidgets.QFrame(self)
        self.bubble.setObjectName("coach")
        self.bubble.setFixedWidth(340)

        self._title = QtWidgets.QLabel(self.bubble)
        self._title.setObjectName("coachTitle")
        self._title.setWordWrap(True)
        self._body = QtWidgets.QLabel(self.bubble)
        self._body.setObjectName("coachBody")
        self._body.setWordWrap(True)
        self._counter = QtWidgets.QLabel(self.bubble)
        self._counter.setObjectName("coachCounter")

        self._btn_skip = QtWidgets.QPushButton("Pular", self.bubble)
        self._btn_prev = QtWidgets.QPushButton("Anterior", self.bubble)
        self._btn_next = QtWidgets.QPushButton("Próximo", self.bubble)
        self._btn_next.setObjectName("primary")
        self._btn_skip.clicked.connect(self.finish)
        self._btn_prev.clicked.connect(self._prev)
        self._btn_next.clicked.connect(self._next)

        lay = QtWidgets.QVBoxLayout(self.bubble)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        lay.addWidget(self._title)
        lay.addWidget(self._body)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._counter)
        row.addStretch(1)
        row.addWidget(self._btn_skip)
        row.addWidget(self._btn_prev)
        row.addWidget(self._btn_next)
        lay.addLayout(row)

        self.setStyleSheet(
            """
            QFrame#coach { background:#ffffff; border:1px solid #e3e7ee;
                           border-radius:12px; }
            QLabel#coachTitle { font-size:16px; font-weight:700; color:#20242b; }
            QLabel#coachBody { font-size:13px; color:#4a515c; }
            QLabel#coachCounter { color:#9aa1ad; font-size:12px; }
            QFrame#coach QPushButton { background:#ffffff; border:1px solid #d4dae3;
                           border-radius:8px; padding:6px 12px; color:#2c313a; }
            QFrame#coach QPushButton:hover { background:#f4f6f9; }
            QFrame#coach QPushButton#primary { background:#ef443b; border:none;
                           color:#ffffff; font-weight:600; }
            QFrame#coach QPushButton#primary:hover { background:#d83a32; }
            """
        )
        self._render()

    # -- geometria ---------------------------------------------------- #
    def _current_target_rect(self) -> QtCore.QRect | None:
        targets = self._steps[self._i].get("targets")
        if not targets:
            return None
        rect = QtCore.QRect()
        for w in targets:
            if w is None or not w.isVisible():
                continue
            # O overlay não é ancestral dos alvos; mapeia via coordenadas globais.
            top_left = self.mapFromGlobal(w.mapToGlobal(QtCore.QPoint(0, 0)))
            r = QtCore.QRect(top_left, w.size())
            rect = r if rect.isNull() else rect.united(r)
        return None if rect.isNull() else rect.adjusted(
            -self.PAD, -self.PAD, self.PAD, self.PAD
        )

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        dim = QtGui.QColor(16, 18, 22, 150)
        full = self.rect()
        hole = self._current_target_rect()

        if hole is None:
            painter.fillRect(full, dim)
        else:
            # Escurece tudo, menos o buraco do elemento destacado.
            painter.fillRect(QtCore.QRect(0, 0, full.width(), hole.top()), dim)
            painter.fillRect(
                QtCore.QRect(0, hole.bottom(), full.width(), full.height() - hole.bottom()),
                dim,
            )
            painter.fillRect(QtCore.QRect(0, hole.top(), hole.left(), hole.height()), dim)
            painter.fillRect(
                QtCore.QRect(hole.right(), hole.top(), full.width() - hole.right(), hole.height()),
                dim,
            )
            pen = QtGui.QPen(QtGui.QColor("#ef443b"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(hole, 8, 8)

    def _place_bubble(self, hole: QtCore.QRect | None) -> None:
        self.bubble.adjustSize()
        bw, bh = self.bubble.width(), self.bubble.height()
        margin = 14
        if hole is None:
            x = (self.width() - bw) // 2
            y = (self.height() - bh) // 2
        else:
            x = min(max(margin, hole.left()), self.width() - bw - margin)
            if hole.bottom() + margin + bh <= self.height():
                y = hole.bottom() + margin
            else:
                y = max(margin, hole.top() - margin - bh)
        self.bubble.move(int(x), int(y))

    # -- navegação ---------------------------------------------------- #
    def _render(self) -> None:
        step = self._steps[self._i]
        self._title.setText(step["title"])
        self._body.setText(step["text"])
        self._counter.setText(f"{self._i + 1}/{len(self._steps)}")
        self._btn_prev.setVisible(self._i > 0)
        last = self._i == len(self._steps) - 1
        self._btn_next.setText("Concluir" if last else "Próximo")
        self._btn_skip.setVisible(not last)
        self.setGeometry(self.parent().rect())
        self._place_bubble(self._current_target_rect())
        self.update()

    def _next(self) -> None:
        if self._i >= len(self._steps) - 1:
            self.finish()
        else:
            self._i += 1
            self._render()

    def _prev(self) -> None:
        if self._i > 0:
            self._i -= 1
            self._render()

    def finish(self) -> None:
        cb, self._on_finish = self._on_finish, None
        self.hide()
        self.deleteLater()
        if cb:
            cb()

    def reposition(self) -> None:
        self.setGeometry(self.parent().rect())
        self._place_bubble(self._current_target_rect())
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Bloqueia cliques no fundo durante o tour (o balão trata os seus).
        event.accept()


# --------------------------------------------------------------------------- #
# Janela principal
# --------------------------------------------------------------------------- #
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, manager: AnyDeskManager | None, exe_hint: Path | None):
        super().__init__()
        self.manager = manager
        self._exe_hint = exe_hint
        self._workers: list[Worker] = []
        self.cards: dict[int, SlotCard] = {}

        self.setWindowTitle(f"AnyDesk Multi — Painel  v{__version__}")
        self.resize(760, 560)
        self._build_ui()
        self._apply_style()

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        if self.manager:
            self.timer.start()
            self.refresh()
        else:
            self.statusBar().showMessage("AnyDesk indisponível.", 0)

        # Tour de primeira execução.
        self._overlay: CoachOverlay | None = None
        if self.manager and not self._load_tour_done():
            QtCore.QTimer.singleShot(600, self._start_tour)

    # ---- construção -------------------------------------------------- #
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # Cabeçalho + conexão rápida
        header = QtWidgets.QHBoxLayout()
        logo = QtWidgets.QLabel(
            '<span style="color:#ef443b;">&#9632;</span>&nbsp; AnyDesk Multi'
        )
        logo.setObjectName("logo")
        logo.setTextFormat(QtCore.Qt.TextFormat.RichText)
        header.addWidget(logo)
        header.addStretch(1)

        self.quick_input = QtWidgets.QLineEdit()
        self.quick_input.setPlaceholderText("ID AnyDesk (ex.: 123 456 789)")
        self.quick_input.setMinimumWidth(220)
        self.quick_input.returnPressed.connect(self._quick_connect)
        self.quick_btn = QtWidgets.QPushButton("Conexão Rápida")
        self.quick_btn.setObjectName("primary")
        self.quick_btn.clicked.connect(self._quick_connect)
        header.addWidget(self.quick_input)
        header.addWidget(self.quick_btn)
        root.addLayout(header)

        # Grade de cards (2 colunas)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(14)
        slots = self.manager.max_slots if self.manager else config.DEFAULT_MAX_SLOTS
        for i in range(1, slots + 1):
            card = SlotCard(i)
            card.connect_requested.connect(self._card_connect)
            card.launch_requested.connect(self._card_launch)
            card.kill_requested.connect(self._card_kill)
            card.clean_requested.connect(self._card_clean)
            self.cards[i] = card
            grid.addWidget(card, (i - 1) // 2, (i - 1) % 2)
        root.addLayout(grid, 1)

        # Rodapé
        footer = QtWidgets.QHBoxLayout()
        self.btn_help = QtWidgets.QPushButton("?")
        self.btn_help.setObjectName("help")
        self.btn_help.setFixedWidth(30)
        self.btn_help.setToolTip("Rever o tutorial")
        self.btn_help.clicked.connect(self._start_tour)
        footer.addWidget(self.btn_help)
        footer.addStretch(1)
        self.btn_refresh = QtWidgets.QPushButton("Atualizar")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_killall = QtWidgets.QPushButton("Encerrar tudo")
        self.btn_killall.setObjectName("danger")
        self.btn_killall.clicked.connect(self._kill_all)
        footer.addWidget(self.btn_refresh)
        footer.addWidget(self.btn_killall)
        root.addLayout(footer)

        self.statusBar().showMessage("Pronto.")

    # ---- estilo ------------------------------------------------------ #
    def _apply_style(self) -> None:
        # Paleta inspirada no AnyDesk: tema claro, limpo, com o vermelho #ef443b.
        self.setStyleSheet(
            """
            QWidget { background:#f2f4f7; color:#20242b;
                      font-family:'Segoe UI',sans-serif; font-size:13px; }
            QMainWindow, QStatusBar { background:#f2f4f7; }
            QLabel#logo { font-size:20px; font-weight:700; color:#20242b; }
            QFrame#card { background:#ffffff; border:1px solid #e3e7ee;
                          border-radius:10px; }
            QLabel#cardTitle { font-size:14px; font-weight:600; color:#20242b; }
            QLabel#idLabel { font-size:26px; font-weight:700; color:#20242b;
                             letter-spacing:1px; }
            QLabel#pids { color:#9aa1ad; font-size:11px; }
            QLabel#state { color:#9aa1ad; }
            QLabel#dotOn  { color:#2bb24c; font-size:15px; }
            QLabel#dotOff { color:#c3c9d2; font-size:15px; }
            QLineEdit { background:#ffffff; border:1px solid #d4dae3;
                        border-radius:8px; padding:8px 11px; color:#20242b; }
            QLineEdit:focus { border:1px solid #ef443b; }
            /* Botão padrão: contorno discreto, fundo branco */
            QPushButton { background:#ffffff; border:1px solid #d4dae3;
                          border-radius:8px; padding:7px 12px; color:#2c313a; }
            QPushButton:hover { background:#f4f6f9; border-color:#c2cad6; }
            QPushButton:disabled { color:#b4bac4; background:#f4f5f7;
                                   border-color:#e6e9ef; }
            /* Primário: vermelho AnyDesk */
            QPushButton#primary { background:#ef443b; border:none; color:#ffffff;
                                  font-weight:600; padding:8px 16px; }
            QPushButton#primary:hover { background:#d83a32; }
            QPushButton#primary:disabled { background:#f3a9a4; color:#ffffff; }
            /* Destrutivo: texto vermelho, contorno suave */
            QPushButton#danger { color:#ef443b; border:1px solid #f3b9b4; }
            QPushButton#danger:hover { background:#fdeceb; }
            QPushButton#danger:disabled { color:#f3a9a4; border-color:#f3d6d3; }
            QPushButton#help { font-weight:700; color:#8a919d; }
            QPushButton#help:hover { background:#eceff3; border-color:#c2cad6; }
            QStatusBar { color:#9aa1ad; }
            QToolTip { background:#20242b; color:#ffffff; border:none; padding:4px; }
            """
        )

    # ---- helpers de estado ------------------------------------------ #
    def _set_all_busy(self, busy: bool) -> None:
        for c in self.cards.values():
            c.set_busy(busy)
        self.quick_btn.setEnabled(not busy)
        self.btn_killall.setEnabled(not busy)

    def _spawn(self, fn, on_done, *args, busy=True, **kwargs) -> None:
        """Roda ``fn`` numa thread e chama ``on_done(result)`` na UI."""
        if busy:
            self._set_all_busy(True)
        w = Worker(fn, *args, **kwargs)
        self._workers.append(w)

        def _cleanup():
            if busy:
                self._set_all_busy(False)
            self.refresh()
            if w in self._workers:
                self._workers.remove(w)

        def _ok(result):
            on_done(result)
            _cleanup()

        def _err(msg):
            self.statusBar().showMessage(f"Erro: {msg}", 6000)
            _cleanup()

        w.done.connect(_ok)
        w.failed.connect(_err)
        w.start()

    # ---- refresh ----------------------------------------------------- #
    def refresh(self) -> None:
        if not self.manager:
            return
        # Scan pode levar ~100-300ms; roda fora da UI sem travar botões.
        w = Worker(self.manager.scan)
        self._workers.append(w)

        def _apply(scan):
            for slot, st in scan.items():
                if slot in self.cards:
                    self.cards[slot].update_status(st)
            running = sum(1 for st in scan.values() if st.running)
            self.statusBar().showMessage(
                f"{running}/{self.manager.max_slots} em execução."
            )
            if w in self._workers:
                self._workers.remove(w)

        w.done.connect(_apply)
        w.failed.connect(lambda m: self.statusBar().showMessage(f"Erro: {m}", 5000))
        w.start()

    # ---- ações ------------------------------------------------------- #
    def _require_manager(self) -> bool:
        if self.manager:
            return True
        self.statusBar().showMessage("AnyDesk indisponível.", 5000)
        return False

    def _quick_connect(self) -> None:
        if not self._require_manager():
            return
        rid = self.quick_input.text().strip()
        if not rid:
            return
        self.statusBar().showMessage(f"Conectando em {rid}…")
        self._spawn(
            self.manager.connect,
            lambda res: self._after_connect(res),
            rid,
        )

    def _after_connect(self, res) -> None:
        ok, msg, slot = res
        self.statusBar().showMessage(
            (f"[slot {slot}] " if slot else "") + msg, 6000
        )
        if ok:
            self.quick_input.clear()

    def _card_connect(self, slot: int) -> None:
        if not self._require_manager():
            return
        rid, ok = QtWidgets.QInputDialog.getText(
            self, f"Conectar — Slot {slot}", "ID AnyDesk do destino:"
        )
        if not ok or not rid.strip():
            return
        self.statusBar().showMessage(f"Slot {slot}: conectando em {rid}…")
        self._spawn(
            self.manager.launch,
            lambda res: self.statusBar().showMessage(res[1], 6000),
            slot, remote_id=rid.strip(),
        )

    def _card_launch(self, slot: int) -> None:
        if not self._require_manager():
            return
        self._spawn(
            self.manager.launch,
            lambda res: self.statusBar().showMessage(res[1], 6000),
            slot,
        )

    def _card_kill(self, slot: int) -> None:
        if not self._require_manager():
            return
        self._spawn(
            self.manager.kill,
            lambda n: self.statusBar().showMessage(
                f"Slot {slot}: {n} processo(s) encerrado(s).", 5000
            ),
            slot,
        )

    def _card_clean(self, slot: int, new_id: bool) -> None:
        if not self._require_manager():
            return
        if new_id:
            ok = QtWidgets.QMessageBox.question(
                self, "Gerar novo ID",
                f"Apagar o perfil do slot {slot} e gerar um AnyDesk ID novo?",
            )
            if ok != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.statusBar().showMessage(f"Slot {slot}: limpando…")
        self._spawn(
            self.manager.clean,
            lambda res: self.statusBar().showMessage(
                f"Slot {slot}: " + "; ".join(res[1]), 7000
            ),
            slot, new_id=new_id,
        )

    def _kill_all(self) -> None:
        if not self._require_manager():
            return
        self._spawn(
            self.manager.kill_all,
            lambda n: self.statusBar().showMessage(
                f"{n} processo(s) encerrado(s) no total.", 5000
            ),
        )

    # ---- tutorial ---------------------------------------------------- #
    def _ui_state_path(self) -> Path:
        base = self.manager.data_dir if self.manager else config.default_data_dir()
        return Path(base) / "ui_state.json"

    def _load_tour_done(self) -> bool:
        try:
            data = json.loads(self._ui_state_path().read_text(encoding="utf-8"))
            return bool(data.get("tour_done"))
        except (OSError, ValueError):
            return False

    def _save_tour_done(self) -> None:
        try:
            p = self._ui_state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"tour_done": True}), encoding="utf-8")
        except OSError:
            pass

    def _tour_steps(self) -> list[dict]:
        c = self.cards[min(self.cards)]
        return [
            {
                "title": "Bem-vindo ao AnyDesk Multi",
                "text": "Rode várias sessões isoladas do AnyDesk ao mesmo tempo, "
                "cada uma com seu próprio ID. Vou mostrar o básico em alguns passos.",
                "targets": None,
            },
            {
                "title": "Conexão Rápida",
                "text": "Digite um ID AnyDesk aqui e clique em Conexão Rápida: o app "
                "conecta no primeiro slot livre automaticamente.",
                "targets": [self.quick_input, self.quick_btn],
            },
            {
                "title": "Os slots",
                "text": "Cada cartão é uma instância isolada do AnyDesk, com ID "
                "próprio. Dá para rodar até quatro ao mesmo tempo, sem uma "
                "atrapalhar a outra.",
                "targets": [c],
            },
            {
                "title": "Abrir e conectar",
                "text": "“Abrir” sobe o slot sem conectar. “Conectar…” pede um ID e "
                "conecta usando esse slot específico.",
                "targets": [c.btn_launch, c.btn_connect],
            },
            {
                "title": "Novo ID e Limpar",
                "text": "“Novo ID” gera um AnyDesk ID novo para o slot. “Limpar” apaga "
                "o cache da sessão, mantendo o ID.",
                "targets": [c.btn_clean, c.btn_newid],
            },
            {
                "title": "Encerrar e atualizar",
                "text": "“Encerrar tudo” fecha todas as sessões de uma vez. "
                "“Atualizar” relê o estado dos slots na hora.",
                "targets": [self.btn_refresh, self.btn_killall],
            },
            {
                "title": "Pronto!",
                "text": "É só isso. Você pode rever este tutorial quando quiser no "
                "botão “?”.",
                "targets": [self.btn_help],
            },
        ]

    def _start_tour(self, *_args) -> None:
        if self._overlay is not None:
            return
        self._overlay = CoachOverlay(self, self._tour_steps(), self._on_tour_finish)
        self._overlay.show()
        self._overlay.raise_()

    def _on_tour_finish(self) -> None:
        self._overlay = None
        self._save_tour_done()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if getattr(self, "_overlay", None) is not None:
            self._overlay.reposition()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.timer.stop()
        for w in self._workers:
            w.wait(1500)
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    icon_path = config.bundled_icon()
    if icon_path is not None:
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    exe = config.find_anydesk_exe()
    manager = None
    if exe is not None:
        try:
            manager = AnyDeskManager(anydesk_exe=str(exe))
        except FileNotFoundError:
            manager = None
    win = MainWindow(manager, exe)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
