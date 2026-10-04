CREATE TABLE IF NOT EXISTS contest_logs (
  id bigserial PRIMARY KEY,
  contest text NOT NULL DEFAULT 'Кубок Сочи 2026',
  my_call text NOT NULL,
  dx_call text NOT NULL,
  band text NOT NULL,
  km integer NOT NULL,
  points integer NOT NULL,
  worked_at timestamp NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (contest, my_call, dx_call, band, worked_at)
);

GRANT SELECT, INSERT ON contest_logs TO sochiham;
GRANT USAGE, SELECT ON SEQUENCE contest_logs_id_seq TO sochiham;
