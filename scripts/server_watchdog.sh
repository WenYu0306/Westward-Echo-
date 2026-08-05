#!/bin/bash
# Westward Echo server watchdog — run in background
# Usage: bash scripts/server_watchdog.sh &
# Checks every 5 minutes: process alive, job health, recent errors

INTERVAL=3600  # 1 hour

while true; do
    TS=$(date "+%H:%M:%S")

    # 1. Process check
    if ! pgrep -f "python.*src.main" > /dev/null; then
        echo "[$TS] ❌ SERVER DOWN — python3 -m src.main not running"
        echo "[$TS] ❌ SERVER DOWN — python3 -m src.main not running" >> /tmp/westward_watchdog.log
        sleep "$INTERVAL"
        continue
    fi

    # 2. Job health (SQLite)
    HEALTH=$(sqlite3 "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）/data/jobs.db" \
        "SELECT status, COUNT(*) FROM jobs WHERE created_at > datetime('now', '-1 hour') GROUP BY status;" 2>/dev/null)

    FAILED_COUNT=$(echo "$HEALTH" | grep "failed" | cut -d'|' -f2)
    TRANSLATING_COUNT=$(echo "$HEALTH" | grep "translating" | cut -d'|' -f2)
    COMPLETE_COUNT=$(echo "$HEALTH" | grep "complete" | cut -d'|' -f2)

    # 3. Recent errors in app.log
    ERRORS=$(grep -c "ERROR\|CRITICAL" "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）/data/app.log" 2>/dev/null | tail -1)

    # Build status line
    STATUS=""
    [ -n "$TRANSLATING_COUNT" ] && STATUS="$STATUS translating=$TRANSLATING_COUNT"
    [ -n "$COMPLETE_COUNT" ] && STATUS="$STATUS complete=$COMPLETE_COUNT"
    [ -n "$FAILED_COUNT" ] && STATUS="$STATUS ❌ failed=$FAILED_COUNT"

    echo "[$TS] ✅ UP |${STATUS} | errors(hour)=${ERRORS:-0}"
    sleep "$INTERVAL"
done
