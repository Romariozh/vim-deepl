#!/usr/bin/env bash

# RUN ->
# ./scripts/sql/trainer_new_debug.sh
# HOURS=6 LIMIT=50 ./scripts/sql/trainer_new_debug.sh
# LANG_FILTER=DA HOURS=24 ./scripts/sql/trainer_new_debug.sh

set -euo pipefail

DB="${DB:-$HOME/.local/share/vim-deepl/vocab.db}"

# Optional knobs
LANG="${LANG_FILTER:-EN}"        # EN by default
HOURS="${HOURS:-1}"              # "last N hours"
LIMIT="${LIMIT:-20}"             # how many recent cards to show

echo "+++ New cards created in last ${HOURS}h (${LANG} only) +++"
sqlite3 "$DB" <<SQL
.headers on
.mode column

WITH now AS (
  SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts
),
cards AS (
  SELECT
    e.src_lang,
    -- created_at can be epoch seconds (numeric text) OR datetime text
    CASE
      WHEN typeof(c.created_at)='text' AND c.created_at GLOB '[0-9]*'
        THEN CAST(c.created_at AS INTEGER)
      ELSE CAST(strftime('%s', c.created_at) AS INTEGER)
    END AS created_ts,
    c.reps,
    c.last_review_at
  FROM training_cards c
  JOIN entries e ON e.id = c.entry_id
  WHERE IFNULL(c.suspended,0)=0
    AND IFNULL(e.ignore,0)=0
    AND e.src_lang = '$LANG'
)
SELECT
  COUNT(*) AS cards_created_last_${HOURS}h,
  SUM(reps=0) AS created_and_unreviewed,
  SUM(reps>0) AS created_and_reviewed
FROM cards
WHERE created_ts >= (SELECT now_ts FROM now) - (${HOURS} * 3600);
SQL

echo
echo "+++ Recently created cards (${LANG} only) [card_id, term, created_local, created_delta, due_local, due_delta] +++"
echo
sqlite3 "$DB" <<SQL
.headers on
.mode column

WITH now AS (
  SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts
),
cards AS (
  SELECT
    c.id AS card_id,
    e.term,
    e.translation,

    CASE
      WHEN typeof(c.created_at)='text' AND c.created_at GLOB '[0-9]*'
        THEN CAST(c.created_at AS INTEGER)
      ELSE CAST(strftime('%s', c.created_at) AS INTEGER)
    END AS created_ts,

    -- Normalize due_at (seconds, ms, or sec*10000 "ticks")
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 10000000000000 THEN CAST(CAST(c.due_at AS INTEGER)/10000 AS INTEGER)
      WHEN CAST(c.due_at AS INTEGER) > 100000000000   THEN CAST(CAST(c.due_at AS INTEGER)/1000  AS INTEGER)
      ELSE CAST(c.due_at AS INTEGER)
    END AS due_ts,

    c.reps,
    c.last_grade
  FROM training_cards c
  JOIN entries e ON e.id = c.entry_id
  WHERE IFNULL(c.suspended,0)=0
    AND IFNULL(e.ignore,0)=0
    AND c.due_at IS NOT NULL
    AND e.src_lang = '$LANG'
)
SELECT
  card_id,
  term,
  datetime(created_ts,'unixepoch','localtime') AS created_local,

  printf('%s%d:%02d:%02d',
    CASE WHEN (created_ts - (SELECT now_ts FROM now)) < 0 THEN '-' ELSE '+' END,
    abs(created_ts - (SELECT now_ts FROM now)) / 86400,
    (abs(created_ts - (SELECT now_ts FROM now)) % 86400) / 3600,
    (abs(created_ts - (SELECT now_ts FROM now)) % 3600) / 60
  ) AS created_delta_dhm,

  datetime(due_ts,'unixepoch','localtime') AS due_local,

  printf('%s%d:%02d:%02d',
    CASE WHEN (due_ts - (SELECT now_ts FROM now)) < 0 THEN '-' ELSE '+' END,
    abs(due_ts - (SELECT now_ts FROM now)) / 86400,
    (abs(due_ts - (SELECT now_ts FROM now)) % 86400) / 3600,
    (abs(due_ts - (SELECT now_ts FROM now)) % 3600) / 60
  ) AS due_delta_dhm,

  reps,
  last_grade
FROM cards
ORDER BY created_ts DESC
LIMIT $LIMIT;
SQL

