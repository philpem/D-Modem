"""Locations shared by the checked-in integration tools.

Keep the reconstruction in a separate checkout; DSP_ROOT selects its location.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DSP_ROOT = Path(os.environ.get('DSP_ROOT', str(ROOT.parent / 'slmodem_dsp_re'))).expanduser().resolve()
