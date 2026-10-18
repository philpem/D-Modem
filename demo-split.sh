#!/bin/sh
set -eu
DEMO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$DEMO_ROOT/tools/presentation_demo.py" --split "$@"
