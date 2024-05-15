#!/bin/bash
set -e

USERNAME=abc
PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "${PGID}" "${USERNAME}" > /dev/null
usermod -o -u "${PUID}" "${USERNAME}" > /dev/null

exec gosu "${USERNAME}" spiderman "$@"
