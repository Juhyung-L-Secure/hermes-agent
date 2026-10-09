#!/bin/sh
set -eu
umask 077
exec python3 -m security.network_proxy
