#!/bin/sh
set -eu

exec python3 -m security.firewall "$@"
