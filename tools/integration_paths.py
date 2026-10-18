"""Locations shared by the checked-in integration tools.

The bundled DSP submodule is the default; DSP_ROOT selects another checkout.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DSP_ROOT = Path(os.environ.get('DSP_ROOT', str(ROOT / 'slmodem_dsp_re'))).expanduser().resolve()
