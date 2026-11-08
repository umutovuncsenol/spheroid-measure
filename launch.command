#!/usr/bin/env bash
# Double-click on macOS to start the app. Works from wherever the project folder is.
cd "$(dirname "$0")"
exec bash launcher/launch.sh "$@"
