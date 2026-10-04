CREATE TABLE IF NOT EXISTS round_host (
  id int PRIMARY KEY,
  callsign text DEFAULT '',
  password text DEFAULT '',
  current_call text DEFAULT '',
  current_name text DEFAULT ''
);
INSERT INTO round_host (id) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS round_queue (
  id serial PRIMARY KEY,
  pos int DEFAULT 0,
  callsign text,
  name text
);
GRANT ALL ON round_host, round_queue TO sochiham;
GRANT USAGE, SELECT ON SEQUENCE round_queue_id_seq TO sochiham;
