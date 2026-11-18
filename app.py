# -*- coding: utf-8 -*-
"""Ponto de entrada do executável (PyInstaller) — abre o painel gráfico.

Usar um script no topo do projeto evita problemas de import relativo que
aconteceriam ao empacotar ``anydesk_multi/gui.py`` diretamente.
"""
from anydesk_multi.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
