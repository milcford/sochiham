CREATE TABLE IF NOT EXISTS locators (
  code text PRIMARY KEY,
  title text NOT NULL
);

INSERT INTO locators (code, title) VALUES
  ('KN95', 'Сочи, Адлер, Хоста'),
  ('KN94', 'Туапсе, Лазаревское'),
  ('KN96', 'восток Большого Сочи'),
  ('KN85', 'Геленджик'),
  ('KN75', 'Новороссийск, Анапа'),
  ('KN84', 'Краснодар'),
  ('KN86', 'север Геленджика'),
  ('KN74', 'запад Новороссийска'),
  ('LN04', 'Майкоп, Армавир'),
  ('LN05', 'Апшеронск'),
  ('LN06', 'восток Краснодара'),
  ('KN97', 'Ростов на Дону')
ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title;

GRANT SELECT ON locators TO sochiham;
