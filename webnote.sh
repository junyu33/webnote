#!/bin/sh
# Portable deployment; requires Docker Engine and the Docker Compose plugin.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$base"
dc() { docker compose --project-directory "$base" --env-file "$base/.env" -f "$base/compose.portable.yaml" "$@"; }
usage() {
    echo 'Usage: ./webnote.sh init URL | load | build | up | down | status | logs | backup ARCHIVE | restore ARCHIVE' >&2
    exit 2
}
action=${1:-}
[ -n "$action" ] || usage
shift
if [ "$action" = init ]; then
    [ "$#" -eq 1 ] || usage
    if [ -e .env ]; then echo '.env already exists; not overwritten.' >&2; exit 1; fi
    printf '%s\n' "$1" | grep -Eq '^https?://[A-Za-z0-9._:-]+/?$' || { echo 'Provide an HTTP(S) origin, without a path or credentials.' >&2; exit 1; }
    umask 077
    token=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    image=webnote:portable
    if [ -f image-tag.txt ]; then image=$(cat image-tag.txt); fi
    # A same-directory rename avoids leaving a partially written configuration.
    tmp=$(mktemp "$base/.env.XXXXXX")
    trap 'rm -f "$tmp"' EXIT HUP INT TERM
    printf 'WEBNOTE_IMAGE=%s\nWEBNOTE_PUBLIC_URL=%s\nWEBNOTE_TOKEN=%s\nWEBNOTE_BIND_PORT=8765\nWEBNOTE_PROJECT=webnote\n' "$image" "${1%/}" "$token" > "$tmp"
    mv "$tmp" .env
    trap - EXIT HUP INT TERM
    echo 'Created .env with a new deletion token. Keep this file private.'
    exit 0
fi
[ -f .env ] || { echo 'Run ./webnote.sh init URL first.' >&2; exit 1; }
# Do not operate on a same-name legacy/other deployment by accident.
case "$action" in
    up|down|status|logs|backup|restore)
        for container in $(dc ps --all -q webnote); do
            config=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$container")
            if [ "$config" != "$base/compose.portable.yaml" ]; then
                echo 'Project already belongs to another Compose deployment. Choose a different WEBNOTE_PROJECT in .env.' >&2
                exit 1
            fi
        done
        ;;
esac
case "$action" in
    load)
        [ "$#" -eq 0 ] || usage
        case $(uname -m) in
            x86_64|amd64) arch=amd64 ;;
            aarch64|arm64) arch=arm64 ;;
            *) echo 'Only linux/amd64 and linux/arm64 image archives are supplied.' >&2; exit 1 ;;
        esac
        docker image load --input "$base/images/webnote-linux-$arch.tar.gz"
        ;;
    build) [ "$#" -eq 0 ] || usage; dc build webnote ;;
    up) [ "$#" -eq 0 ] || usage; dc up -d --no-build --wait --wait-timeout 60 ;;
    down) [ "$#" -eq 0 ] || usage; dc down ;; # Named data volume is retained.
    status) [ "$#" -eq 0 ] || usage; dc ps ;;
    logs) [ "$#" -eq 0 ] || usage; dc logs --tail 50 webnote ;;
    backup)
        [ "$#" -eq 1 ] || usage
        output=$1
        case "$output" in /*) ;; *) output="$base/$output" ;; esac
        [ ! -e "$output" ] || { echo 'Backup destination already exists.' >&2; exit 1; }
        umask 077
        tmp=$(mktemp "$output.XXXXXX")
        running=$(dc ps --status running -q webnote)
        cleanup() {
            result=$?
            trap - EXIT HUP INT TERM
            rm -f "$tmp"
            if [ -n "$running" ]; then dc start webnote >&2 || result=1; fi
            exit "$result"
        }
        trap cleanup EXIT HUP INT TERM
        if [ -n "$running" ]; then dc stop webnote >&2; fi
        dc run --rm --no-deps -T webnote python -c '
import os, sys, tarfile
os.umask(0o077)
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    archive.add("/data", arcname=".")
' > "$tmp"
        mv "$tmp" "$output"
        echo "Data backup: $output (save .env separately and privately)." >&2
        ;;
    restore)
        [ "$#" -eq 1 ] || usage
        [ -f "$1" ] || { echo 'Backup archive not found.' >&2; exit 1; }
        # Stop first; never overwrite a populated volume. A failed restore stays stopped.
        dc stop webnote >&2
        dc run --rm --no-deps -T webnote python -c '
import os, sys, tarfile
from pathlib import Path
os.umask(0o077)
root = Path("/data")
if any(root.iterdir()):
    raise SystemExit("Restore refused: data volume must be empty.")
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
    archive.extractall(root, filter="data")
print("Data restored. Run ./webnote.sh up to start the service.")
' < "$1"
        ;;
    *) usage ;;
esac
