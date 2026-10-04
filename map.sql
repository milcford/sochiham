ALTER TABLE contest_logs ADD COLUMN IF NOT EXISTS my_lat double precision;
ALTER TABLE contest_logs ADD COLUMN IF NOT EXISTS my_lon double precision;
