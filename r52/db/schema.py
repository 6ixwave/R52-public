"""Миграции схемы. Применённую миграцию нельзя менять: только добавлять новую."""

V1_INITIAL = """
CREATE TABLE source(
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('marketplace','retail','manual','test')),
  collector TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED'
) STRICT;
INSERT INTO source(key, name, kind) VALUES
  ('WB', 'Wildberries', 'marketplace'),
  ('OZON', 'Ozon', 'marketplace'),
  ('AVITO', 'Авито', 'marketplace'),
  ('ALIEXPRESS', 'AliExpress', 'marketplace'),
  ('FARPOST', 'Фарпост', 'marketplace'),
  ('MANUAL', 'Вручную', 'manual');

CREATE TABLE product(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL REFERENCES source(key),
  listing_id TEXT NOT NULL,
  url TEXT,
  title TEXT NOT NULL,
  brand TEXT,
  model TEXT,
  origin TEXT NOT NULL,
  search_text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_observation_id INTEGER,
  last_seen_at TEXT,
  last_currency TEXT,
  last_regular_minor INTEGER,
  last_lowest_minor INTEGER,
  last_lowest_role TEXT,
  last_availability TEXT NOT NULL DEFAULT 'UNKNOWN',
  observation_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(source, listing_id)
) STRICT;
CREATE INDEX product_updated ON product(updated_at);

CREATE TABLE observation(
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  observed_at TEXT,
  imported_at TEXT NOT NULL,
  origin TEXT NOT NULL,
  evidence_level TEXT NOT NULL,
  availability TEXT NOT NULL DEFAULT 'UNKNOWN'
    CHECK(availability IN('IN_STOCK','OUT_OF_STOCK','UNKNOWN')),
  fingerprint TEXT NOT NULL UNIQUE,
  normalized_json TEXT NOT NULL CHECK(json_valid(normalized_json)),
  raw_json TEXT CHECK(raw_json IS NULL OR json_valid(raw_json))
) STRICT;
CREATE INDEX observation_product ON observation(product_id, observed_at);
CREATE INDEX observation_imported ON observation(imported_at);

CREATE TABLE price_point(
  id INTEGER PRIMARY KEY,
  observation_id INTEGER NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN('regular','card','wallet','coupon','old','unknown')),
  amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
  currency TEXT NOT NULL,
  label TEXT,
  observed_at TEXT,
  UNIQUE(observation_id, role, amount_minor, currency)
) STRICT;
CREATE INDEX price_point_product ON price_point(product_id, observed_at);

CREATE TABLE inbox_item(
  id INTEGER PRIMARY KEY,
  content_sha256 TEXT NOT NULL UNIQUE,
  filename TEXT,
  origin TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('PENDING','ACCEPTED','REJECTED','INVALID')),
  error_code TEXT,
  payload_json TEXT CHECK(payload_json IS NULL OR json_valid(payload_json)),
  preview_json TEXT CHECK(preview_json IS NULL OR json_valid(preview_json)),
  decided_at TEXT,
  observation_id INTEGER REFERENCES observation(id) ON DELETE SET NULL
) STRICT;
CREATE INDEX inbox_status ON inbox_item(status, received_at);

CREATE TABLE intake_file(
  path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  content_sha256 TEXT,
  result TEXT NOT NULL,
  seen_at TEXT NOT NULL
) STRICT;

CREATE TABLE favorite(
  product_id INTEGER PRIMARY KEY REFERENCES product(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  note TEXT
) STRICT;

CREATE TABLE compare_group(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;
CREATE TABLE compare_item(
  group_id INTEGER NOT NULL REFERENCES compare_group(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  added_at TEXT NOT NULL,
  PRIMARY KEY(group_id, product_id)
) STRICT;

CREATE TABLE watch(
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL UNIQUE REFERENCES product(id) ON DELETE CASCADE,
  target_minor INTEGER CHECK(target_minor IS NULL OR target_minor >= 0),
  currency TEXT NOT NULL DEFAULT 'RUB',
  price_basis TEXT NOT NULL DEFAULT 'regular' CHECK(price_basis IN('regular','lowest_shown')),
  drop_percent INTEGER CHECK(drop_percent IS NULL OR drop_percent BETWEEN 1 AND 90),
  notify_availability INTEGER NOT NULL DEFAULT 1 CHECK(notify_availability IN(0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN(0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_observation_id INTEGER
) STRICT;

CREATE TABLE event(
  id INTEGER PRIMARY KEY,
  fact_key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  product_id INTEGER REFERENCES product(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX event_created ON event(created_at);

CREATE TABLE notification(
  id INTEGER PRIMARY KEY,
  event_id INTEGER UNIQUE REFERENCES event(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES product(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('PENDING','DEFERRED_QUIET','SHOWN','READ','DISMISSED')),
  created_at TEXT NOT NULL,
  shown_at TEXT,
  read_at TEXT
) STRICT;
CREATE INDEX notification_status ON notification(status, id);

CREATE TABLE task(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  origin TEXT NOT NULL CHECK(origin IN('USER','SCHEDULE','SYSTEM','ASSISTANT')),
  params_json TEXT NOT NULL CHECK(json_valid(params_json)),
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN('QUEUED','RUNNING','COMPLETED','PARTIAL','FAILED','CANCELLED','INTERRUPTED','BLOCKED')),
  attempt INTEGER NOT NULL DEFAULT 1,
  progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  deadline_at TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN(0,1)),
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  error_code TEXT,
  error_message TEXT
) STRICT;
CREATE INDEX task_status ON task(status, created_at);
CREATE TABLE task_transition(
  id INTEGER PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  before TEXT,
  after TEXT NOT NULL,
  at TEXT NOT NULL,
  note TEXT
) STRICT;
CREATE INDEX task_transition_task ON task_transition(task_id, id);

CREATE TABLE schedule(
  key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  params_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(params_json)),
  rule TEXT NOT NULL CHECK(rule IN('interval','daily')),
  interval_seconds INTEGER CHECK(interval_seconds IS NULL OR interval_seconds >= 60),
  daily_time TEXT CHECK(daily_time IS NULL OR daily_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
  next_due_at TEXT NOT NULL,
  last_task_id TEXT,
  last_run_at TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE setting(
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL CHECK(json_valid(value_json)),
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE chat_session(
  id TEXT PRIMARY KEY,
  title TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE chat_message(
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN('user','assistant')),
  text TEXT NOT NULL,
  data_json TEXT CHECK(data_json IS NULL OR json_valid(data_json)),
  idempotency_key TEXT UNIQUE,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX chat_message_session ON chat_message(session_id, id);

CREATE TABLE assistant_action(
  token TEXT PRIMARY KEY,
  session_id TEXT REFERENCES chat_session(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  params_json TEXT NOT NULL CHECK(json_valid(params_json)),
  status TEXT NOT NULL CHECK(status IN('PROPOSED','DONE','EXPIRED')),
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  created_at TEXT NOT NULL,
  done_at TEXT
) STRICT;

CREATE TABLE import_run(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  source_path TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  stats_json TEXT CHECK(stats_json IS NULL OR json_valid(stats_json))
) STRICT;
"""

V2_MEDIA = """
ALTER TABLE product ADD COLUMN image_data TEXT;
ALTER TABLE product ADD COLUMN image_source_url TEXT;
ALTER TABLE product ADD COLUMN image_observed_at TEXT;
ALTER TABLE product ADD COLUMN category_path TEXT;
"""

V3_GALLERY = """
ALTER TABLE product ADD COLUMN gallery_json TEXT CHECK(gallery_json IS NULL OR json_valid(gallery_json));
"""

V4_ANALYSIS_REQUEST = """
CREATE TABLE assistant_analysis_request(
  key TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL,
  result_json TEXT
) STRICT;
CREATE INDEX analysis_request_created ON assistant_analysis_request(created_at);

CREATE TABLE assistant_llm_call(
  id INTEGER PRIMARY KEY,
  model TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('PENDING','DONE','FAILED')),
  reserve_usd REAL,
  usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
  cost_usd REAL
) STRICT;
CREATE INDEX llm_call_created ON assistant_llm_call(created_at);
"""

V5_ANALYSIS_REQUEST_JSON = """
ALTER TABLE assistant_analysis_request ADD COLUMN request_json TEXT CHECK(request_json IS NULL OR json_valid(request_json));
"""

V6_ANALYSIS_HISTORY = """
ALTER TABLE assistant_analysis_request ADD COLUMN scope_json TEXT CHECK(scope_json IS NULL OR json_valid(scope_json));
UPDATE assistant_analysis_request
SET scope_json = COALESCE(json_extract(result_json, '$.facts[0].product_ids'), '[]')
WHERE result_json IS NOT NULL AND scope_json IS NULL;
"""

V7_ASSISTANT_REQUEST = """
CREATE TABLE assistant_request(
  key TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('STARTED','DONE','FAILED')),
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX assistant_request_created ON assistant_request(created_at);
"""

V8_LLM_CALL_UNKNOWN = """
CREATE TABLE assistant_llm_call_new(
  id INTEGER PRIMARY KEY,
  model TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('PENDING','DONE','FAILED','UNKNOWN')),
  reserve_usd REAL,
  usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
  cost_usd REAL
) STRICT;
INSERT INTO assistant_llm_call_new
  SELECT id, model, created_at, status, reserve_usd, usage_json, cost_usd
  FROM assistant_llm_call;
DROP TABLE assistant_llm_call;
ALTER TABLE assistant_llm_call_new RENAME TO assistant_llm_call;
CREATE INDEX llm_call_created ON assistant_llm_call(created_at);
"""

V9_AUTOCOLLECT = """
CREATE TABLE auto_source(
 source TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
 last_run TEXT, last_result TEXT, retry_after TEXT
) STRICT;
INSERT INTO auto_source(source) VALUES ('WB'),('OZON'),('AVITO'),('FARPOST'),('ALIEXPRESS'),('ALIEXPRESS_RU');
CREATE TABLE auto_target(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL REFERENCES auto_source(source),
 kind TEXT NOT NULL CHECK(kind IN('query','category','url')), value TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN(0,1)), item_limit INTEGER NOT NULL CHECK(item_limit BETWEEN 1 AND 50),
 interval_seconds INTEGER CHECK(interval_seconds BETWEEN 60 AND 2592000), last_run TEXT,
 page INTEGER NOT NULL DEFAULT 1, UNIQUE(source,kind,value)
) STRICT;
CREATE TABLE auto_refresh(product_id INTEGER PRIMARY KEY REFERENCES product(id) ON DELETE CASCADE, attempted TEXT NOT NULL) STRICT;
CREATE TABLE auto_delivery(
 path TEXT PRIMARY KEY, digest TEXT NOT NULL, source TEXT NOT NULL, listing_id TEXT NOT NULL
) STRICT;
CREATE INDEX auto_delivery_listing ON auto_delivery(source,listing_id);
"""

V10_BROAD_SEARCH = """
ALTER TABLE auto_target ADD COLUMN origin TEXT NOT NULL DEFAULT 'USER'
 CHECK(origin IN('USER','CATALOG'));
INSERT OR IGNORE INTO auto_target(source,kind,value,enabled,item_limit,origin)
 SELECT s.source,'query',q.value,1,5,'CATALOG' FROM auto_source s CROSS JOIN (
 SELECT 'смартфоны' AS value UNION ALL SELECT 'ноутбуки'
 UNION ALL SELECT 'видеокарты' UNION ALL SELECT 'мониторы'
 UNION ALL SELECT 'бытовая техника' UNION ALL SELECT 'одежда'
 UNION ALL SELECT 'обувь' UNION ALL SELECT 'мебель'
 UNION ALL SELECT 'инструменты' UNION ALL SELECT 'автозапчасти'
 UNION ALL SELECT 'спортивные товары' UNION ALL SELECT 'игрушки'
 UNION ALL SELECT 'косметика' UNION ALL SELECT 'товары для животных'
 UNION ALL SELECT 'книги' UNION ALL SELECT 'продукты питания'
 ) q;
"""

MIGRATIONS = [
    (1, "initial", V1_INITIAL),
    (2, "product_media", V2_MEDIA),
    (3, "product_gallery", V3_GALLERY),
    (4, "analysis_request", V4_ANALYSIS_REQUEST),
    (5, "analysis_request_json", V5_ANALYSIS_REQUEST_JSON),
    (6, "analysis_history", V6_ANALYSIS_HISTORY),
    (7, "assistant_request", V7_ASSISTANT_REQUEST),
    (8, "llm_call_unknown", V8_LLM_CALL_UNKNOWN),
    (9, "autocollect", V9_AUTOCOLLECT),
    (10, "broad_search", V10_BROAD_SEARCH),
]
