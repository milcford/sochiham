-- База портала радиолюбителей.

CREATE TABLE operators (
  id            bigserial PRIMARY KEY,
  callsign      text NOT NULL UNIQUE,
  surname       text,
  name          text NOT NULL,
  patronymic    text,
  city          text,
  locator       text,
  phone         text,
  about         text,
  is_host       boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE round_tables (
  id            bigserial PRIMARY KEY,
  starts_at     timestamptz NOT NULL,
  frequency     text NOT NULL,
  host_id       bigint REFERENCES operators(id),
  current_id    bigint REFERENCES operators(id),
  status        text NOT NULL DEFAULT 'planned'
);

CREATE TABLE checkins (
  id            bigserial PRIMARY KEY,
  table_id      bigint NOT NULL REFERENCES round_tables(id),
  operator_id   bigint NOT NULL REFERENCES operators(id),
  checked_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (table_id, operator_id)
);

CREATE TABLE ads (
  id            bigserial PRIMARY KEY,
  operator_id   bigint REFERENCES operators(id),
  kind          text NOT NULL,
  title         text NOT NULL,
  place         text,
  price         text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE photos (
  id            bigserial PRIMARY KEY,
  operator_id   bigint REFERENCES operators(id),
  path          text NOT NULL,
  caption       text,
  taken_on      date,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE videos (
  id            bigserial PRIMARY KEY,
  operator_id   bigint REFERENCES operators(id),
  path          text NOT NULL,
  caption       text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE contest_entries (
  id            bigserial PRIMARY KEY,
  contest       text NOT NULL,
  operator_id   bigint NOT NULL REFERENCES operators(id),
  dx_call       text NOT NULL,
  dx_locator    text NOT NULL,
  band          text NOT NULL,
  km            numeric(8,1),
  points        integer NOT NULL,
  worked_at     timestamptz,
  UNIQUE (contest, operator_id, dx_call, band)
);

CREATE INDEX ads_created_idx ON ads (created_at DESC);
CREATE INDEX photos_created_idx ON photos (created_at DESC);
