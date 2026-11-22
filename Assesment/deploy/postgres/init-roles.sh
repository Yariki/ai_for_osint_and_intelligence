#!/bin/sh
# Creates the application role. Run by the PostgreSQL image once, when the
# data volume is initialized; it does not run again on later starts.
#
# The application role is separate from the role that owns the schema
# (POSTGRES_USER). It gets its table privileges from the migrations, which
# give it no way to change the audit log. It is created here and not in a
# migration because its password is a secret.
set -eu

: "${NDM_APP_DB_ROLE:?NDM_APP_DB_ROLE is not set}"
: "${NDM_APP_DB_PASSWORD:?NDM_APP_DB_PASSWORD is not set}"

# psql quotes the two values itself (:"role" as an identifier, :'password' as
# a literal), so neither can break out of the statement.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v role="$NDM_APP_DB_ROLE" -v password="$NDM_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE :"role" LOGIN PASSWORD :'password';
SQL
