#!/bin/sh
set -eu

respaw_state_dir="${RESPAW_DATA_DIR:-$HOME/Library/Application Support/ResPaw}"
export ARDUINO_DIRECTORIES_DATA="$respaw_state_dir/arduino/data"
export ARDUINO_DIRECTORIES_DOWNLOADS="$respaw_state_dir/arduino/downloads"
export ARDUINO_DIRECTORIES_USER="$respaw_state_dir/arduino/user"
respaw_cli="${ARDUINO_CLI:-arduino-cli}"
if ! command -v "$respaw_cli" >/dev/null 2>&1 && [ -x "$respaw_state_dir/tools/arduino-cli" ]; then
  respaw_cli="$respaw_state_dir/tools/arduino-cli"
fi
exec "$respaw_cli" "$@"
