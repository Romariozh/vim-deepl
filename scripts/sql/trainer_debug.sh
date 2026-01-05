#!/usr/bin/env bash
set -euo pipefail

DB="${DB:-$HOME/.local/share/vim-deepl/vocab.db}"

# Header width (matches the longest Summary separator line)
# HDR_WIDTH=${HDR_WIDTH:-145}
# Header width: prefer explicit HDR_WIDTH, else terminal width, else fallback.
# Clamp to minimum 80 so headers stay readable.
_term_cols="${COLUMNS:-}"
if [[ -z "${_term_cols}" ]] && command -v tput >/dev/null 2>&1; then
  _term_cols="$(tput cols 2>/dev/null || true)"
fi

HDR_WIDTH="${HDR_WIDTH:-${_term_cols:-145}}"

# If not a number -> fallback
if ! [[ "$HDR_WIDTH" =~ ^[0-9]+$ ]]; then
  HDR_WIDTH=145
fi

# Clamp min 80
if (( HDR_WIDTH < 80 )); then
  HDR_WIDTH=80
fi

_hdr_repeat() {
  local n="$1"
  local ch="$2"
  # repeat single char n times
  printf '%*s' "$n" '' | tr ' ' "$ch"
}

hdr() {
  local title="$1"
  local w="$HDR_WIDTH"
  local inner=" ${title} "
  local len=${#inner}

  if (( len >= w )); then
    echo "$inner"
    return
  fi

  local pad=$(( w - len ))
  local left=$(( pad / 2 ))
  local right=$(( pad - left ))

  local l="$(_hdr_repeat "$left" '+')"
  local r="$(_hdr_repeat "$right" '+')"
  echo "${l}${inner}${r}"
}

hdr "Top due NOW (EN only) [card_id, term, translation, due_local, delta_s]"
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
    e.translation,
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 10000000000000 THEN CAST(CAST(c.due_at AS INTEGER)/10000 AS INTEGER)
      WHEN CAST(c.due_at AS INTEGER) > 100000000000   THEN CAST(CAST(c.due_at AS INTEGER)/1000  AS INTEGER)
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
  translation,
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
hdr "Next due (EN only) [card_id, term, translation, due_local, delta_s]"
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
    e.translation,
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 10000000000000 THEN CAST(CAST(c.due_at AS INTEGER)/10000 AS INTEGER)
      WHEN CAST(c.due_at AS INTEGER) > 100000000000   THEN CAST(CAST(c.due_at AS INTEGER)/1000  AS INTEGER)
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
  translation,
  datetime(due_ts,'unixepoch','localtime') AS due_local,
  (due_ts - (SELECT now_ts FROM now)) AS delta_s,
  printf('%s%d:%02d:%02d',
    CASE WHEN (due_ts - (SELECT now_ts FROM now)) < 0 THEN '-' ELSE '+' END,
    abs(due_ts - (SELECT now_ts FROM now)) / 86400,
    (abs(due_ts - (SELECT now_ts FROM now)) % 86400) / 3600,
    (abs(due_ts - (SELECT now_ts FROM now)) % 3600) / 60
  ) AS delta_dhm
FROM cards
WHERE due_ts > (SELECT now_ts FROM now)
ORDER BY due_ts ASC
LIMIT 10;
SQL

echo
hdr "Top HARD (EN only) [card_id, term, lapses, wrong_streak, correct_streak, hard_score, due_local, due_delta_s]"
sqlite3 "$DB" <<'SQL'
.headers on
.mode column
WITH now AS (SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts),
cards AS (
  SELECT c.id AS card_id, e.term, c.lapses, c.wrong_streak, c.correct_streak, e.src_lang,
        CASE
            WHEN CAST(c.due_at AS INTEGER) > 10000000000000 THEN CAST(CAST(c.due_at AS INTEGER)/10000 AS INTEGER)
            WHEN CAST(c.due_at AS INTEGER) > 100000000000   THEN CAST(CAST(c.due_at AS INTEGER)/1000  AS INTEGER)
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
  correct_streak,
  (wrong_streak*100 + lapses*10 - IFNULL(correct_streak,0)) AS hard_score,
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
  AND (
    wrong_streak > 0
    OR (lapses > 0 AND IFNULL(correct_streak,0) < 2)
  )
ORDER BY
  hard_score DESC,
  (due_ts > (SELECT now_ts FROM now)) ASC,
  ABS(due_ts - (SELECT now_ts FROM now)) ASC,
  due_ts ASC
LIMIT 10;
SQL

echo
hdr "Next HARD (EN only) [card_id, term, lapses, wrong_streak, correct_streak, due_local, due_delta_s]"
sqlite3 "$DB" <<'SQL'
.headers on
.mode column
WITH now AS (SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts),
cards AS (
  SELECT
    c.id AS card_id,
    e.term,
    c.lapses,
    c.wrong_streak,
    c.correct_streak,
    e.src_lang,
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 10000000000000 THEN CAST(CAST(c.due_at AS INTEGER)/10000 AS INTEGER)
      WHEN CAST(c.due_at AS INTEGER) > 100000000000   THEN CAST(CAST(c.due_at AS INTEGER)/1000  AS INTEGER)
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
  correct_streak,
  datetime(due_ts,'unixepoch','localtime') AS due_local,
  (due_ts - (SELECT now_ts FROM now)) AS due_delta_s,
  printf('%s%d:%02d:%02d',
    CASE WHEN (due_ts - (SELECT now_ts FROM now)) < 0 THEN '-' ELSE '+' END,
    abs(due_ts - (SELECT now_ts FROM now)) / 86400,
    (abs(due_ts - (SELECT now_ts FROM now)) % 86400) / 3600,
    (abs(due_ts - (SELECT now_ts FROM now)) % 3600) / 60
  ) AS due_delta_dhm
FROM cards
WHERE src_lang='EN'
  AND (
    wrong_streak > 0
    OR (lapses > 0 AND IFNULL(correct_streak,0) < 2)
  )
ORDER BY
  (due_ts > (SELECT now_ts FROM now)) ASC,
  ABS(due_ts - (SELECT now_ts FROM now)) ASC,
  due_ts ASC
LIMIT 10;
SQL

echo
hdr "[ Summary (EN only) ]"
sqlite3 "$DB" <<'SQL'
.headers on
.mode column

WITH now AS (
  SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts
),
cards AS (
  SELECT
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 10000000000000 THEN CAST(CAST(c.due_at AS INTEGER)/10000 AS INTEGER)
      WHEN CAST(c.due_at AS INTEGER) > 100000000000   THEN CAST(CAST(c.due_at AS INTEGER)/1000  AS INTEGER)
      ELSE CAST(c.due_at AS INTEGER)
    END AS due_ts,
    c.reps,
    c.last_review_at,
    c.lapses,
    c.wrong_streak,
    c.correct_streak,
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

  -- hard = wrong_streak>0 OR (lapses>0 AND correct_streak<2)
  SUM((wrong_streak>0 OR (lapses>0 AND IFNULL(correct_streak,0) < 2))) AS hard_total,
  SUM((wrong_streak>0 OR (lapses>0 AND IFNULL(correct_streak,0) < 2))
      AND due_ts <= (SELECT now_ts FROM now)) AS hard_due,
  SUM((wrong_streak>0 OR (lapses>0 AND IFNULL(correct_streak,0) < 2))
      AND due_ts  > (SELECT now_ts FROM now)) AS hard_future,

  SUM(reps=0 AND last_review_at IS NULL) AS new_unreviewed_cards,

  -- new: lapses history vs active-hard-by-lapses
  SUM(CAST(lapses AS INTEGER) > 0) AS lapses_gt0,
  SUM(CAST(lapses AS INTEGER) > 0 AND IFNULL(CAST(correct_streak AS INTEGER),0) < 2) AS still_hard_by_lapses,

  (SELECT COUNT(*)
     FROM entries e
     LEFT JOIN training_cards c ON c.entry_id = e.id
    WHERE IFNULL(e.ignore,0)=0
      AND e.src_lang='EN'
      AND c.id IS NULL
  ) AS entries_without_card
FROM cards;
SQL

echo
hdr "Streak distribution (EN only)"
sqlite3 "$DB" <<'SQL'
.headers off
.mode list

DROP TABLE IF EXISTS tmp_buckets;

CREATE TEMP TABLE tmp_buckets AS
SELECT
  CASE
    WHEN IFNULL(CAST(c.correct_streak AS INTEGER),0) >= 7 THEN '7+'
    ELSE CAST(IFNULL(CAST(c.correct_streak AS INTEGER),0) AS TEXT)
  END AS streak_bucket,
  COUNT(*) AS cards,
  CASE
    WHEN IFNULL(CAST(c.correct_streak AS INTEGER),0) >= 7 THEN 999
    ELSE IFNULL(CAST(c.correct_streak AS INTEGER),0)
  END AS ord
FROM training_cards c
JOIN entries e ON e.id=c.entry_id
WHERE IFNULL(c.suspended,0)=0
  AND IFNULL(e.ignore,0)=0
  AND e.src_lang='EN'
GROUP BY streak_bucket, ord;

-- Two-line aligned output only
SELECT
  'streak: ' || (SELECT group_concat(printf('%4s', streak_bucket), ' ')
                 FROM (SELECT streak_bucket FROM tmp_buckets ORDER BY ord));
SELECT
  'cards : ' || (SELECT group_concat(printf('%4d', cards), ' ')
                 FROM (SELECT cards FROM tmp_buckets ORDER BY ord));

DROP TABLE tmp_buckets;
SQL

echo
hdr "Almost mastered (EN only) [streak 5-6]"
sqlite3 "$DB" <<'SQL'
.headers on
.mode column

WITH now AS (SELECT CAST(strftime('%s','now') AS INTEGER) AS now_ts),
cards AS (
  SELECT
    c.id AS card_id,
    e.term,
    e.translation,
    CAST(c.correct_streak AS INTEGER) AS correct_streak,
    CASE
      WHEN CAST(c.due_at AS INTEGER) > 10000000000000 THEN CAST(CAST(c.due_at AS INTEGER)/10000 AS INTEGER)
      WHEN CAST(c.due_at AS INTEGER) > 100000000000   THEN CAST(CAST(c.due_at AS INTEGER)/1000  AS INTEGER)
      ELSE CAST(c.due_at AS INTEGER)
    END AS due_ts
  FROM training_cards c
  JOIN entries e ON e.id=c.entry_id
  WHERE IFNULL(c.suspended,0)=0
    AND IFNULL(e.ignore,0)=0
    AND e.src_lang='EN'
    AND IFNULL(CAST(c.correct_streak AS INTEGER),0) BETWEEN 5 AND 6
)
SELECT
  card_id,
  term,
  translation,
  correct_streak,
  datetime(due_ts,'unixepoch','localtime') AS due_local,

  printf('%s%d:%02d:%02d',
    CASE WHEN (due_ts - (SELECT now_ts FROM now)) < 0 THEN '-' ELSE '+' END,
    abs(due_ts - (SELECT now_ts FROM now)) / 86400,
    (abs(due_ts - (SELECT now_ts FROM now)) % 86400) / 3600,
    (abs(due_ts - (SELECT now_ts FROM now)) % 3600) / 60
  ) AS due_delta_dhm

FROM cards
ORDER BY due_ts ASC, card_id ASC
LIMIT 20;
SQL
