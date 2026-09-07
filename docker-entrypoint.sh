#!/bin/sh
set -eu

data_dir="${PAPER_STUDIO_DATA_DIR:-/data}"
config_dir="${PAPER_STUDIO_CONFIG_DIR:-/config}"

mkdir -p "$data_dir" "$config_dir"

# Docker Compose binds local directories here. Initialise their roots as the
# unprivileged application user, then never run the web process as root.
if [ "$(id -u)" = "0" ]; then
    chown paperstudio:paperstudio "$data_dir" "$config_dir"
    exec gosu paperstudio "$@"
fi

exec "$@"
