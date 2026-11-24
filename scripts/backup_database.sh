#!/usr/bin/env bash
# Back up the Norwich Urban Traffic Simulator database to one file.
#
#   scripts/backup_database.sh [DATABASE_URL] [OUTPUT_FILE]
#
# DATABASE_URL defaults to $DATABASE_URL (for Render, use the database's
# External Database URL). OUTPUT_FILE defaults to
# backups/norwich_sim-<UTC date and time>.dump.
#
# The file is pg_dump's custom format (compressed; restore it with
# scripts/restore_database.sh). It holds every table, including
# alembic_version, so a restored database knows its schema version and
# `alembic upgrade head` brings an older backup up to date.
#
# Needs pg_dump and pg_restore of the server's major version or newer
# (PostgreSQL 16 here: `brew install postgresql@16`, or
# `apt install postgresql-client-16`).

set -euo pipefail

url="${1:-${DATABASE_URL:-}}"
if [[ -z "$url" ]]; then
    echo "Usage: $0 DATABASE_URL [OUTPUT_FILE]   (or set DATABASE_URL)" >&2
    exit 2
fi
# SQLAlchemy's driver suffix means nothing to pg_dump
url="${url/postgresql+psycopg:\/\//postgresql://}"

output="${2:-backups/norwich_sim-$(date -u +%Y%m%dT%H%M%SZ).dump}"
mkdir -p "$(dirname "$output")"

# Write to a temporary name first, so a failed dump never leaves a file
# that looks like a good backup
partial="$output.partial"
trap 'rm -f "$partial"' EXIT

pg_dump --format=custom --no-owner --no-privileges --file="$partial" --dbname="$url"

# The dump must be readable, and contain the tables the app needs
contents="$(pg_restore --list "$partial")"
for table in alembic_version simulation_runs; do
    if ! grep -q "TABLE public $table " <<<"$contents"; then
        echo "Backup check failed: table $table is missing from the dump" >&2
        exit 1
    fi
done

mv "$partial" "$output"
trap - EXIT
echo "Backed up to $output ($(du -h "$output" | cut -f1 | tr -d ' '))"
