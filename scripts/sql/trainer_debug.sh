#!/usr/bin/env bash
set -euo pipefail

DB="${DB:-$HOME/.local/share/vim-deepl/vocab.db}"

echo "+++ Top due NOW (EN only) [card_id, term, due_local, delta_s] +++"
sqlite3 "$DB" <<'SQL'
.headers on
.mode column

WITH now AS (
  SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts
),
cards AS (
  SELECT
    c.id AS card_id,
    e.term,
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 100000000000
      THEN CAST(CAST(c.due_at AS INTEGER)/1000 AS INTEGER)
      ELSE CAST(c.due_at AS INTEGER)
    END AS due_ts
  FROM training_cards c
  JOIN entries e ON e.id=c.entry_id
  WHERE IFNULL(c.suspended,0)=0
    AND IFNULL(e.ignore,0)=0
    AND c.due_at IS NOT NULL
    AND e.src_lang='EN'
)
SELECT
  card_id,
  term,
  datetime(due_ts,'unixepoch','localtime') AS due_local,

  (due_ts - (SELECT now_ts FROM now))      AS delta_s,

  printf('%s%d:%02d:%02d',
    CASE WHEN (due_ts - (SELECT now_ts FROM now)) < 0 THEN '-' ELSE '+' END,
    abs(due_ts - (SELECT now_ts FROM now)) / 86400,
    (abs(due_ts - (SELECT now_ts FROM now)) % 86400) / 3600,
    (abs(due_ts - (SELECT now_ts FROM now)) % 3600) / 60
  ) AS delta_dhm
FROM cards
WHERE due_ts <= (SELECT now_ts FROM now)
ORDER BY due_ts ASC
LIMIT 10;
SQL

echo
echo "+++ Top HARD (EN only) [card_id, term, lapses, wrong_streak, due_local, due_delta_s] +++"
sqlite3 "$DB" <<'SQL'
.headers on
.mode column
WITH now AS (SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts),
cards AS (
  SELECT c.id AS card_id, e.term, c.lapses, c.wrong_streak, e.src_lang,
         CASE
           WHEN CAST(c.due_at AS INTEGER) > 100000000000
           THEN CAST(CAST(c.due_at AS INTEGER)/1000 AS INTEGER)
           ELSE CAST(c.due_at AS INTEGER)
         END AS due_ts
  FROM training_cards c
  JOIN entries e ON e.id=c.entry_id
  WHERE IFNULL(c.suspended,0)=0
    AND IFNULL(e.ignore,0)=0
    AND c.due_at IS NOT NULL
)
SELECT
  card_id,
  term,
  lapses,
  wrong_streak,
  datetime(due_ts,'unixepoch','localtime') AS due_local,

  (due_ts - (SELECT now_ts FROM now))      AS due_delta_s,

  printf('%s%d:%02d:%02d',
    CASE WHEN (due_ts - (SELECT now_ts FROM now)) < 0 THEN '-' ELSE '+' END,
    abs(due_ts - (SELECT now_ts FROM now)) / 86400,
    (abs(due_ts - (SELECT now_ts FROM now)) % 86400) / 3600,
    (abs(due_ts - (SELECT now_ts FROM now)) % 3600) / 60
  ) AS due_delta_dhm
FROM cards
WHERE src_lang='EN'
  AND (lapses > 0 OR wrong_streak > 0)
ORDER BY
  lapses DESC,
  wrong_streak DESC,
  ABS(due_ts - (SELECT now_ts FROM now)) ASC
LIMIT 10;
SQL

echo
echo "+++ [ Summary (EN only) ] +++"
sqlite3 "$DB" <<'SQL'
.headers on
.mode column

WITH now AS (
  SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts
),
cards AS (
  SELECT
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 100000000000
      THEN CAST(CAST(c.due_at AS INTEGER)/1000 AS INTEGER)
      ELSE CAST(c.due_at AS INTEGER)
    END AS due_ts,
    c.reps,
    c.last_review_at,
    c.lapses,
    c.wrong_streak,
    e.src_lang
  FROM training_cards c
  JOIN entries e ON e.id=c.entry_id
  WHERE IFNULL(c.suspended,0)=0
    AND IFNULL(e.ignore,0)=0
    AND c.due_at IS NOT NULL
    AND e.src_lang='EN'
)
SELECT
  COUNT(*) AS cards_total,
  SUM(due_ts <= (SELECT now_ts FROM now)) AS due_now,
  SUM(due_ts  > (SELECT now_ts FROM now)) AS due_future,

  SUM((lapses>0 OR wrong_streak>0)) AS hard_total,
  SUM((lapses>0 OR wrong_streak>0) AND due_ts <= (SELECT now_ts FROM now)) AS hard_due,
  SUM((lapses>0 OR wrong_streak>0) AND due_ts  > (SELECT now_ts FROM now)) AS hard_future,

  SUM(reps=0 AND last_review_at IS NULL) AS new_unreviewed_cards,

  (SELECT COUNT(*)
     FROM entries e
     LEFT JOIN training_cards c ON c.entry_id = e.id
    WHERE IFNULL(e.ignore,0)=0
      AND e.src_lang='EN'
      AND c.id IS NULL
  ) AS entries_without_card
FROM cards;
SQL

