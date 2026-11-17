#!/usr/bin/env bash
# Restore a backup made by scripts/backup_database.sh.
#
#   scripts/restore_database.sh BACKUP_FILE [DATABASE_URL] --yes
#
# DATABASE_URL defaults to $DATABASE_URL. This REPLACES the NORWICH//SIM
# tables in that database with the backup's (other tables are left
# alone), so it asks for --yes. Stop the backend first, then run
# `alembic upgrade head` afterwards, which brings a backup made before a
# newer migration up to date (it does nothing otherwise).

set -euo pipefail

file="" url="" confirmed=0
for argument in "$@"; do
    case "$argument" in
        --yes) confirmed=1 ;;
        *) if [[ -z "$file" ]]; then file="$argument"; else url="$argument"; fi ;;
    esac
done
url="${url:-${DATABASE_URL:-}}"

if [[ -z "$file" || -z "$url" ]]; then
    echo "Usage: $0 BACKUP_FILE [DATABASE_URL] --yes   (or set DATABASE_URL)" >&2
    exit 2
fi
if [[ ! -f "$file" ]]; then
    echo "No such backup: $file" >&2
    exit 2
fi
if [[ "$confirmed" != 1 ]]; then
    echo "This replaces the NORWICH//SIM tables in the target database. Add --yes to go ahead." >&2
    exit 2
fi
url="${url/postgresql+psycopg:\/\//postgresql://}"

# Check the file before touching the database
pg_restore --list "$file" >/dev/null

# One transaction: either the whole backup is restored or nothing changes
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction \
    --exit-on-error --dbname="$url" "$file"

echo "Restored $file. Now run: DATABASE_URL=... alembic upgrade head"
