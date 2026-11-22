#!/bin/sh
# Mounted over the PostgreSQL image's own /docker-entrypoint-initdb.d/10_postgis.sh,
# which would install PostGIS, postgis_topology, fuzzystrmatch and the TIGER
# geocoder into the application database, and a template database besides.
# The product uses PostGIS alone, and migration 0003 installs it itself. The
# other extensions add some 60 tables and two schemas on the search path that
# nothing uses, and every backup would carry them.
#
# Mounted the same way by the integration tests
# (backend/tests/integration/conftest.py), so they test this database.
true
