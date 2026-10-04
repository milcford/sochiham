CREATE TABLE IF NOT EXISTS locators (
  code text PRIMARY KEY,
  title text NOT NULL
);

DELETE FROM locators WHERE code IN ('KN95','KN96','KN85','KN75','KN84','KN86','KN74','LN05','LN06');

INSERT INTO locators (code, title) VALUES
  ('KN93', 'Сочи, Адлер, Хоста, Лазаревское'),
  ('KN94', 'Туапсе, Геленджик, Апшеронск'),
  ('KN84', 'Новороссийск, Анапа'),
  ('KN95', 'Краснодар'),
  ('LN03', 'Красная Поляна'),
  ('LN04', 'Майкоп'),
  ('KN97', 'Ростов на Дону')
ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title;

GRANT SELECT ON locators TO sochiham;
