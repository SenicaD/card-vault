#!/usr/bin/env bash
# Deploy a committed CardVault revision to the existing UGOS Compose project.
set -euo pipefail

usage() {
    printf 'Usage: bash scripts/deploy-ugreen.sh [--check | <git-revision>]\n'
}

if (( $# > 1 )); then usage >&2; exit 2; fi
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --check) mode=check ;;
    -*) usage >&2; exit 2 ;;
    *) mode=deploy ;;
esac

nas=${CARDVAULT_NAS_SSH:-senicad@ugreen-nas-1}
project_dir=${CARDVAULT_NAS_PROJECT_DIR:-/volume1/docker/projects}
nas_url=${CARDVAULT_NAS_LOCAL_URL:-http://127.0.0.1:3020}
revision=
archive=/dev/null

if [[ $mode == deploy ]]; then
    repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
    revision=$(git -C "$repo" rev-parse --verify --end-of-options "${1:-HEAD}^{commit}")
    archive=$(mktemp)
    trap 'rm -f -- "$archive"' EXIT
    git -C "$repo" archive --format=tar "$revision" .gitignore README.md docker-compose.yml backend frontend > "$archive"
fi

remote_script=$(cat <<'REMOTE'
set -euo pipefail
umask 077
mode=$1
revision=$2
cd -- "$3"
url=$4
project_dir=$PWD
backend=cardvault-backend
frontend=cardvault-frontend
services=("$backend" "$frontend")
data_dir=cardvault-data
source_dir=cardvault

compose() {
    docker compose -p projects -f docker-compose.yaml "$@"
}

image_revision() {
    docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1"
}

verify() {
    python3 - "$url" "$data_dir" "$@" <<'PYTHON'
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import quote
from urllib.request import urlopen

base_url, directory, action = sys.argv[1:4]
data_dir = Path(directory)


def fetch(path):
    return urlopen(base_url.rstrip("/") + path, timeout=10)


def get_json(path):
    with fetch(path) as response:
        return json.load(response)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def file_hash(path):
    with path.open("rb") as file:
        digest = hashlib.sha256()
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


def inventory_ids():
    with sqlite3.connect(f"file:{data_dir}/cardvault.db?mode=ro", uri=True) as db:
        require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite integrity check failed")
        return [row[0] for row in db.execute("SELECT id FROM card ORDER BY id")]


if action == "snapshot":
    paths = sorted(path for path in (data_dir / "images").rglob("*") if path.is_file())
    json.dump({"cards": inventory_ids(), "photos": {str(path): file_hash(path) for path in paths}}, sys.stdout)
    sys.exit()

with fetch("/") as response:
    require(re.search(r"<title>\s*CardVault\s*</title>", response.read().decode()), "CardVault frontend did not load")
health = get_json("/api/health")
require(health.get("ok") is True, "CardVault API did not report healthy")
modes = {key: health[key] for key in ("identifier", "ebay")}
if action == "baseline":
    print(json.dumps(modes))
    sys.exit()

require(modes == json.loads(sys.argv[4]), "Card identification or eBay mode changed")
require(isinstance(get_json("/api/settings"), dict), "CardVault settings did not load")
cards = get_json("/api/cards")
snapshot = json.loads(Path(sys.argv[5]).read_text())
ids = set(snapshot["cards"])
require(ids.issubset(card["id"] for card in cards), "An inventory record is missing from the API")
require(ids.issubset(inventory_ids()), "An inventory record is missing from SQLite")
for path, expected in snapshot["photos"].items():
    require(Path(path).is_file() and file_hash(Path(path)) == expected, f"A saved photo changed or is missing: {path}")

image = next((card["image_url"] for card in cards if (card.get("image_url") or "").startswith("/images/")), None)
if not image and snapshot["photos"]:
    # Some stored photos can be unassigned to an inventory item.
    photo = Path(next(iter(snapshot["photos"]))).relative_to(data_dir / "images")
    image = "/images/" + quote(str(photo))
if image:
    with fetch(image) as response:
        require(response.headers.get_content_type().startswith("image/") and response.read(16), "A card photo could not be served")

print(f"Verified frontend, settings, SQLite, {len(cards)} inventory records, and {len(snapshot['photos'])} photo checksums.")
print(f"Identification: {modes['identifier']}. eBay: {modes['ebay']}.")
PYTHON
}

for tool in docker flock python3 sha256sum tar; do
    if ! command -v "$tool" >/dev/null; then
        printf 'Missing NAS command: %s\n' "$tool" >&2
        exit 1
    fi
done
compose config --quiet
test -d "$source_dir/backend"
test -d "$source_dir/frontend"
for service in "${services[@]}"; do
    labels=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}} {{index .Config.Labels "com.docker.compose.project.config_files"}}' "$service")
    if [[ $labels != "projects $service $project_dir/docker-compose.yaml" ]]; then
        printf 'Unexpected Compose project for %s: %s\n' "$service" "$labels" >&2
        exit 1
    fi
done
mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}} {{.Source}}{{end}}{{end}}' "$backend")
if [[ $mount != "bind $project_dir/$data_dir" || ! -s $data_dir/cardvault.db || ! -d $data_dir/images ]]; then
    printf 'Could not confirm the CardVault inventory and photo bind mount.\n' >&2
    exit 1
fi

if [[ $mode == check ]]; then
    modes=$(verify baseline)
    printf 'CardVault is reachable. Modes: %s\n' "$modes"
    compose ps "${services[@]}"
    exit 0
fi

install -d -m 700 cardvault-backups
exec 9>cardvault-backups/.deploy.lock
if ! flock -n 9; then
    printf 'Another CardVault deployment is using this NAS project.\n' >&2
    exit 1
fi

modes=$(verify baseline)
backend_image=$(docker inspect --format '{{.Config.Image}}' "$backend")
frontend_image=$(docker inspect --format '{{.Config.Image}}' "$frontend")
old_backend=$(docker inspect --format '{{.Image}}' "$backend")
old_frontend=$(docker inspect --format '{{.Image}}' "$frontend")
if [[ $backend_image != projects-cardvault-backend || $frontend_image != projects-cardvault-frontend ]]; then
    printf 'Unexpected image names in the running CardVault Compose project.\n' >&2
    exit 1
fi

short_revision=${revision:0:7}
backup_dir=$(mktemp -d "$project_dir/cardvault-backups/$(date -u +%Y%m%dT%H%M%SZ)-$short_revision-XXXXXX")
stage_dir=$backup_dir/staged-source
old_source=$backup_dir/previous-source
suffix=${backup_dir##*/}
suffix=${suffix,,}
staged_backend=$backend_image:staged-$suffix
staged_frontend=$frontend_image:staged-$suffix

start_services() {
    compose up --detach --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 120 "${services[@]}"
}

save_backup() {
    local archive_name=$1
    verify snapshot > "$backup_dir/inventory.json"
    tar -czf "$backup_dir/$archive_name" "$data_dir"
    tar -dzf "$backup_dir/$archive_name"
    (
        cd -- "$backup_dir"
        sha256sum "$archive_name" > "$archive_name.sha256"
        sha256sum -c "$archive_name.sha256"
    )
    printf 'Verified backup: %s/%s\n' "$backup_dir" "$archive_name"
}

backend_paused=0
cutover_started=0
recreate_started=0
recover() {
    local result=$? recovery_failed=0
    trap - EXIT HUP INT TERM
    if (( result == 0 )); then return; fi
    set +e
    if (( cutover_started )); then
        printf 'Cutover failed. Restoring the previous CardVault source and images.\n' >&2
        if [[ -d $old_source ]]; then
            if [[ -d $source_dir ]]; then mv -T -- "$source_dir" "$stage_dir" || recovery_failed=1; fi
            mv -T -- "$old_source" "$source_dir" || recovery_failed=1
        fi
        docker tag "$old_backend" "$backend_image" || recovery_failed=1
        docker tag "$old_frontend" "$frontend_image" || recovery_failed=1
    fi
    if (( recreate_started )); then
        start_services && verify deployed "$modes" "$backup_dir/inventory.json" || recovery_failed=1
    elif (( backend_paused )); then
        docker start "$backend" >/dev/null || recovery_failed=1
    fi
    if (( recovery_failed )); then
        printf 'Automatic recovery needs attention. Backup directory: %s\n' "$backup_dir" >&2
    elif (( cutover_started )); then
        printf 'Previous CardVault source and images restored. Backup directory: %s\n' "$backup_dir" >&2
    else
        printf 'Deployment stopped before cutover. Backup directory: %s\n' "$backup_dir" >&2
    fi
    exit "$result"
}
trap recover EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Backing up CardVault before deploying %s.\n' "$short_revision"
backend_paused=1
docker stop --time 30 "$backend" >/dev/null
save_backup cardvault-data.tar.gz
docker start "$backend" >/dev/null
backend_paused=0

mkdir -- "$stage_dir"
tar -xf - -C "$stage_dir"
test -f "$stage_dir/backend/Dockerfile"
test -f "$stage_dir/frontend/Dockerfile"

# Build isolated tags with the NAS project configuration, including its proxy target.
python3 - "$stage_dir" "$staged_backend" "$staged_frontend" "$revision" > "$backup_dir/staged-compose.json" <<'PYTHON'
import json
import sys

stage, backend, frontend, revision = sys.argv[1:]
services = {}
for name, image in (("backend", backend), ("frontend", frontend)):
    services[f"cardvault-{name}"] = {
        "image": image,
        "build": {"context": f"{stage}/{name}", "labels": {"org.opencontainers.image.revision": revision}},
    }
json.dump({"services": services}, sys.stdout)
PYTHON
compose -f "$backup_dir/staged-compose.json" build "${services[@]}"
test "$(image_revision "$staged_backend")" = "$revision"
test "$(image_revision "$staged_frontend")" = "$revision"

docker tag "$old_backend" "$backend_image:rollback-$suffix"
docker tag "$old_frontend" "$frontend_image:rollback-$suffix"

printf 'Switching CardVault to %s.\n' "$short_revision"
backend_paused=1
docker stop --time 30 "$backend" >/dev/null
# Save a second consistent snapshot if the inventory or photos changed during the build.
if ! tar -dzf "$backup_dir/cardvault-data.tar.gz" > "$backup_dir/pre-cutover-compare.log" 2>&1; then
    save_backup cardvault-data-pre-cutover.tar.gz
fi

cutover_started=1
chmod 755 "$stage_dir"
mv -T -- "$source_dir" "$old_source"
mv -T -- "$stage_dir" "$source_dir"
docker tag "$staged_backend" "$backend_image"
docker tag "$staged_frontend" "$frontend_image"
recreate_started=1
start_services
for service in "${services[@]}"; do
    test "$(image_revision "$(docker inspect --format '{{.Image}}' "$service")")" = "$revision"
done
verify deployed "$modes" "$backup_dir/inventory.json"
compose ps "${services[@]}"
printf '%s\n' "$revision" > "$backup_dir/deployed-revision"
printf 'Deployed %s. Backups and rollback source: %s\n' "$short_revision" "$backup_dir"
REMOTE
)

# OpenSSH passes the command through the NAS login shell, so quote each argument.
remote_command=bash
for arg in -c "$remote_script" -- "$mode" "$revision" "$project_dir" "$nas_url"; do
    remote_command+=" '${arg//\'/\'\\\'\'}'"
done
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -- "$nas" "$remote_command" < "$archive"
