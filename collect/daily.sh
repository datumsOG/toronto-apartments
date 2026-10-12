#!/bin/bash
# The daily collection: run every registered adapter, write app/data/availability.json,
# publish the map. Cron runs this at 06:10 Toronto (EDT). Output goes to collect/daily.log.
# A source that fails or is refused is recorded in the run's own table; the map then shows
# it as stale after 48 hours instead of showing old vacancies as current.
cd (internal path) || exit 1
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ)"
# The home route (the tablet at the house) is used only for hosts an adapter names.
export APARTMENTS_PROXY=http://127.0.0.1:1056
python3 -m collect.run && ./publish.sh
echo "exit $?"
