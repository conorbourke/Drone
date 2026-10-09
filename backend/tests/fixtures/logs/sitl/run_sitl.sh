#!/bin/bash
# Fly one scripted QuadPlane mission in ArduPlane SITL and keep the DataFlash log.
# usage: ARDUPILOT=/path/to/ardupilot PYTHON=/path/to/python-with-pymavlink \
#        ./run_sitl.sh <frame> <rundir> <cruise wall-seconds> [PARAM=value ...]
# e.g.   ./run_sitl.sh quadplane /tmp/run_quad 40
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FRAME=$1; RUN=$2; CRUISE=$3; shift 3
mkdir -p "$RUN" && cd "$RUN" && rm -rf logs eeprom.bin terrain
"$ARDUPILOT/build/sitl/bin/arduplane" -w -M "$FRAME" \
  --defaults "$ARDUPILOT/Tools/autotest/default_params/quadplane.parm,$HERE/logging.parm" \
  -O -35.363261,149.165230,584,353 --speedup 5 > sitl.out 2>&1 &
SITL_PID=$!
sleep 3
timeout 1200 "${PYTHON:-python3}" -u "$HERE/fly_quadplane.py" tcp:127.0.0.1:5760 "$CRUISE" "$@" 2>&1 \
  | grep --line-buffered -v "EOF on TCP\|Connection reset"
sleep 2
kill $SITL_PID
wait $SITL_PID 2>/dev/null
ls -la logs
