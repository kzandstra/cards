CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS sets(
  id TEXT PRIMARY KEY, name_en TEXT, name_fr TEXT, release_date TEXT, printed_total INTEGER,
  released_in_french INTEGER, sort_order INTEGER);
CREATE TABLE IF NOT EXISTS cards(
  id TEXT PRIMARY KEY, set_id TEXT REFERENCES sets(id), number TEXT, number_sort INTEGER,
  name_en TEXT, name_fr TEXT, supertype TEXT, rarity TEXT, is_holo INTEGER, types TEXT, hp TEXT,
  artist TEXT, dex_number INTEGER, image_small TEXT, image_large TEXT);
CREATE TABLE IF NOT EXISTS prices(
  card_id TEXT PRIMARY KEY REFERENCES cards(id),
  cardmarket_avg30_eur REAL, cardmarket_trend_eur REAL, tcgplayer_market_usd REAL,
  ref_price_eur REAL, ref_source TEXT, price_nm_cad REAL, price_date TEXT);
CREATE TABLE IF NOT EXISTS conditions(
  code TEXT PRIMARY KEY, sort_order INTEGER, name_en TEXT, name_fr TEXT, multiplier REAL,
  corners TEXT, edges TEXT, surface TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS collection(
  card_id TEXT PRIMARY KEY REFERENCES cards(id),
  owned INTEGER DEFAULT 0, quantity INTEGER DEFAULT 0, condition TEXT REFERENCES conditions(code),
  language TEXT DEFAULT 'FR', first_edition INTEGER DEFAULT 0, notes TEXT, updated_at TEXT);
CREATE VIEW IF NOT EXISTS v_card_prices AS
SELECT ca.id AS card_id, s.name_fr AS set_fr, s.name_en AS set_en, ca.number, ca.name_fr, ca.name_en, ca.rarity,
       ca.is_holo, p.ref_price_eur, p.ref_source, p.price_nm_cad,
       ROUND(p.price_nm_cad*1.05,2) AS cad_MT, ROUND(p.price_nm_cad*1.00,2) AS cad_NM, ROUND(p.price_nm_cad*0.85,2) AS cad_EX,
       ROUND(p.price_nm_cad*0.70,2) AS cad_GD, ROUND(p.price_nm_cad*0.55,2) AS cad_LP, ROUND(p.price_nm_cad*0.40,2) AS cad_PL,
       ROUND(p.price_nm_cad*0.25,2) AS cad_PO, p.price_date
FROM cards ca JOIN sets s ON s.id=ca.set_id LEFT JOIN prices p ON p.card_id=ca.id
ORDER BY s.sort_order, ca.number_sort;
CREATE VIEW IF NOT EXISTS v_my_collection AS
SELECT ca.id AS card_id, s.name_fr AS set_fr, ca.number, ca.name_fr, ca.name_en, ca.rarity, ca.is_holo,
       col.quantity, col.condition, cond.name_fr AS condition_fr, col.language, col.first_edition, col.notes,
       p.price_nm_cad,
       ROUND(p.price_nm_cad * COALESCE(cond.multiplier,1.0) *
             CASE WHEN col.language='FR' THEN (SELECT CAST(value AS REAL) FROM settings WHERE key='fr_factor') ELSE 1.0 END, 2) AS unit_value_cad,
       ROUND(p.price_nm_cad * COALESCE(cond.multiplier,1.0) *
             CASE WHEN col.language='FR' THEN (SELECT CAST(value AS REAL) FROM settings WHERE key='fr_factor') ELSE 1.0 END
             * col.quantity, 2) AS total_value_cad
FROM collection col JOIN cards ca ON ca.id=col.card_id JOIN sets s ON s.id=ca.set_id
LEFT JOIN prices p ON p.card_id=ca.id LEFT JOIN conditions cond ON cond.code=col.condition
WHERE col.owned=1
ORDER BY total_value_cad DESC;
CREATE VIEW IF NOT EXISTS v_set_progress AS
SELECT s.id, s.name_fr, s.name_en, s.release_date, s.released_in_french,
       COUNT(ca.id) AS cards_in_set, SUM(col.owned) AS owned,
       ROUND(100.0*SUM(col.owned)/COUNT(ca.id),1) AS pct_complete,
       ROUND(SUM(CASE WHEN col.owned=1 THEN p.price_nm_cad*col.quantity ELSE 0 END),2) AS owned_value_nm_cad
FROM sets s JOIN cards ca ON ca.set_id=s.id JOIN collection col ON col.card_id=ca.id LEFT JOIN prices p ON p.card_id=ca.id
GROUP BY s.id ORDER BY s.sort_order;

-- ---------- sales module (user data: migrations must preserve these) ----------
-- listings.quantity = copies still for sale; status: active | sold | cancelled.
CREATE TABLE IF NOT EXISTS listings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL REFERENCES cards(id),
  quantity INTEGER NOT NULL DEFAULT 1,
  condition TEXT REFERENCES conditions(code),
  language TEXT DEFAULT 'FR', first_edition INTEGER DEFAULT 0,
  asking_price_cad REAL,            -- per card
  platform TEXT, url TEXT, notes TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_listings_card ON listings(card_id, status);
-- sales.sale_price_cad / fees_cad / shipping_cad are totals for the whole line (all copies).
-- ref_value_cad = estimated value (app/pricing.py) at the time of sale; removed_qty = copies taken out of `collection`.
CREATE TABLE IF NOT EXISTS sales(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  listing_id INTEGER REFERENCES listings(id),
  card_id TEXT NOT NULL REFERENCES cards(id),
  quantity INTEGER NOT NULL DEFAULT 1,
  condition TEXT REFERENCES conditions(code),
  language TEXT DEFAULT 'FR', first_edition INTEGER DEFAULT 0,
  sale_price_cad REAL NOT NULL, fees_cad REAL NOT NULL DEFAULT 0, shipping_cad REAL NOT NULL DEFAULT 0,
  ref_value_cad REAL, removed_qty INTEGER NOT NULL DEFAULT 0,
  platform TEXT, buyer TEXT, sold_at TEXT NOT NULL, notes TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_sales_sold_at ON sales(sold_at);
CREATE VIEW IF NOT EXISTS v_sales AS
SELECT sa.id, sa.sold_at, sa.platform, sa.buyer, sa.card_id, s.name_fr AS set_fr, ca.number, ca.name_fr, ca.name_en,
       sa.quantity, sa.condition, sa.language, sa.first_edition, sa.sale_price_cad, sa.fees_cad, sa.shipping_cad,
       ROUND(sa.sale_price_cad - sa.fees_cad - sa.shipping_cad, 2) AS net_cad, sa.ref_value_cad, sa.listing_id, sa.notes
FROM sales sa JOIN cards ca ON ca.id=sa.card_id JOIN sets s ON s.id=ca.set_id
ORDER BY sa.sold_at DESC, sa.id DESC;
