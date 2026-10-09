-- cs-postgres store schema and seed data. Runs once, when the data volume is empty
-- (mounted into /docker-entrypoint-initdb.d by infrastructure/compose/testbed.yml).

CREATE TABLE products (
    id          integer PRIMARY KEY,
    sku         text    NOT NULL UNIQUE,
    name        text    NOT NULL,
    price_cents integer NOT NULL CHECK (price_cents > 0)
);

CREATE TABLE carts (
    id         uuid        PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cart_items (
    cart_id    uuid    NOT NULL REFERENCES carts (id) ON DELETE CASCADE,
    product_id integer NOT NULL REFERENCES products (id),
    quantity   integer NOT NULL CHECK (quantity BETWEEN 1 AND 100),
    PRIMARY KEY (cart_id, product_id)
);

CREATE TABLE orders (
    id           uuid        PRIMARY KEY,
    cart_id      uuid        NOT NULL REFERENCES carts (id),
    total_cents  integer     NOT NULL CHECK (total_cents >= 0),
    status       text        NOT NULL CHECK (status IN ('pending', 'paid', 'payment_failed', 'fulfilled')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    fulfilled_at timestamptz
);

CREATE INDEX orders_created_at_idx ON orders (created_at DESC);

INSERT INTO products (id, sku, name, price_cents) VALUES
    (1,  'MUG-001', 'Enamel mug',           1200),
    (2,  'MUG-002', 'Travel mug',           2400),
    (3,  'TEE-001', 'Cotton t-shirt',       1800),
    (4,  'TEE-002', 'Long-sleeve t-shirt',  2600),
    (5,  'HAT-001', 'Wool beanie',          1500),
    (6,  'BAG-001', 'Canvas tote',          1400),
    (7,  'BAG-002', 'Daypack',              5400),
    (8,  'NTB-001', 'Dot-grid notebook',     900),
    (9,  'PEN-001', 'Gel pen, 3-pack',       600),
    (10, 'STK-001', 'Sticker sheet',         400),
    (11, 'BTL-001', 'Steel water bottle',   2200),
    (12, 'SCK-001', 'Merino socks',         1600),
    (13, 'HDY-001', 'Zip hoodie',           4800),
    (14, 'CAP-001', 'Baseball cap',         2000),
    (15, 'PIN-001', 'Enamel pin',            700),
    (16, 'MAT-001', 'Desk mat',             2800),
    (17, 'CBL-001', 'Braided USB-C cable',  1300),
    (18, 'LMP-001', 'Clip-on lamp',         3500),
    (19, 'PLN-001', 'Weekly planner',       1700),
    (20, 'UMB-001', 'Compact umbrella',     2500);
