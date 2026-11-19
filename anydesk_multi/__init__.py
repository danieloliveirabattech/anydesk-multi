# -*- coding: utf-8 -*-
"""anydesk-multi: várias instâncias isoladas do AnyDesk, lado a lado."""
from __future__ import annotations

from .config import DEFAULT_MAX_SLOTS, find_anydesk_exe
from .manager import AnyDeskManager, SlotStatus

__version__ = "0.2.0"
__all__ = ["AnyDeskManager", "SlotStatus", "find_anydesk_exe", "DEFAULT_MAX_SLOTS"]
