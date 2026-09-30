-- DragQuery 样例业务库
-- 五张表 + 外键关系：customers 1:N orders 1:N order_items N:1 products，
-- orders 1:N payments。故意构造跨层级数据，便于核对去重/放大行为。
CREATE SCHEMA IF NOT EXISTS biz;

DROP TABLE IF EXISTS biz.payments, biz.order_items, biz.orders,
                       biz.products, biz.customers CASCADE;

CREATE TABLE biz.customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    region      TEXT NOT NULL,   -- 大区
    province    TEXT NOT NULL,   -- 省
    city        TEXT NOT NULL,   -- 市
    segment     TEXT NOT NULL,   -- 客户分层
    created_at  DATE NOT NULL
);

CREATE TABLE biz.products (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,  -- 品类
    brand        TEXT NOT NULL,
    list_price   NUMERIC(12,2) NOT NULL,
    cost         NUMERIC(12,2) NOT NULL,
    launch_date  DATE NOT NULL
);

CREATE TABLE biz.orders (
    id            SERIAL PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES biz.customers(id),
    order_date    DATE NOT NULL,
    status        TEXT NOT NULL,   -- paid / pending / cancelled
    channel       TEXT NOT NULL,   -- web / app / store
    total_amount  NUMERIC(12,2) NOT NULL,
    discount      NUMERIC(12,2) NOT NULL DEFAULT 0
);

CREATE TABLE biz.order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES biz.orders(id),
    product_id  INTEGER NOT NULL REFERENCES biz.products(id),
    quantity    INTEGER NOT NULL,
    unit_price  NUMERIC(12,2) NOT NULL
);

CREATE TABLE biz.payments (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES biz.orders(id),
    method     TEXT NOT NULL,      -- alipay / wechat / card
    amount     NUMERIC(12,2) NOT NULL,
    paid_at    DATE NOT NULL
);

-- ---------- 客户：覆盖 大区->省->市 层级 ----------
INSERT INTO biz.customers (id, name, region, province, city, segment, created_at) VALUES
 (1, '华东甲公司', '华东', '上海', '上海', 'KA',   '2022-01-10'),
 (2, '华东乙商行', '华东', '江苏', '南京', 'SMB',  '2022-03-22'),
 (3, '华北丙集团', '华北', '北京', '北京', 'KA',   '2022-02-18'),
 (4, '华北丁门店', '华北', '河北', '石家庄','SMB', '2023-05-01'),
 (5, '华南戊科技', '华南', '广东', '深圳', 'KA',   '2022-11-11'),
 (6, '华南己百货', '华南', '广东', '广州', 'SMB',  '2023-07-07'),
 (7, '西南庚贸易', '西南', '四川', '成都', 'SMB',  '2024-01-15'),
 (8, '华东辛网店', '华东', '浙江', '杭州', 'KA',   '2024-02-20');

SELECT setval('biz.customers_id_seq', 8);

-- ---------- 商品：品类 -> 品牌 -> 商品 ----------
INSERT INTO biz.products (id, name, category, brand, list_price, cost, launch_date) VALUES
 (1, '机械键盘 K1',   '外设', 'KeyNova',  399.00, 220.00, '2023-01-01'),
 (2, '无线鼠标 M2',   '外设', 'KeyNova',  149.00,  70.00, '2023-02-01'),
 (3, '显示器 D27',    '外设', 'ViewMax', 1299.00, 820.00, '2023-03-01'),
 (4, '办公椅 A1',     '家具', 'SitWell',  899.00, 430.00, '2023-04-01'),
 (5, '升降桌 H2',     '家具', 'SitWell', 1599.00, 900.00, '2023-05-01'),
 (6, '笔记本电脑 L14','电脑', 'ByteBook',5999.00,4500.00, '2024-01-10'),
 (7, '平板电脑 P11',  '电脑', 'ByteBook',2999.00,2300.00, '2024-02-10'),
 (8, '扩展坞 U7',     '配件', 'LinkPro',  249.00, 110.00, '2024-03-10'),
 (9, '网线 2m',       '配件', 'LinkPro',   29.00,  12.00, '2023-06-01'),
 (10,'USB 充电器 65W','配件', 'LinkPro',  129.00,  55.00, '2023-08-01');

SELECT setval('biz.products_id_seq', 10);

-- ---------- 订单：跨 2024 各季度，含多明细/多支付 ----------
INSERT INTO biz.orders (id, customer_id, order_date, status, channel, total_amount, discount) VALUES
 (1001, 1, DATE '2024-01-05', 'paid',      'web',   1297.00,   0.00),
 (1002, 2, DATE '2024-01-20', 'paid',      'app',    548.00,  50.00),
 (1003, 3, DATE '2024-02-11', 'pending',   'web',   5999.00,   0.00),
 (1004, 4, DATE '2024-02-25', 'cancelled', 'store',  899.00,   0.00),
 (1005, 5, DATE '2024-03-08', 'paid',      'web',   4298.00, 100.00),
 (1006, 6, DATE '2024-04-02', 'paid',      'app',   1628.00,   0.00),
 (1007, 7, DATE '2024-04-19', 'pending',   'store',  249.00,   0.00),
 (1008, 8, DATE '2024-05-06', 'paid',      'web',   3128.00,  20.00),
 (1009, 1, DATE '2024-06-15', 'paid',      'app',    727.00,   0.00),
 (1010, 3, DATE '2024-07-01', 'paid',      'web',   7598.00, 200.00),
 (1011, 5, DATE '2024-08-18', 'paid',      'app',    399.00,   0.00),
 (1012, 2, DATE '2024-09-03', 'pending',   'web',   1299.00,   0.00),
 (1013, 8, DATE '2024-10-12', 'paid',      'store', 1048.00,   0.00),
 (1014, 6, DATE '2024-11-02', 'paid',      'web',   2999.00,   0.00),
 (1015, 7, DATE '2024-12-21', 'paid',      'app',    158.00,   0.00);

SELECT setval('biz.orders_id_seq', 1015);

-- ---------- 明细：部分订单含多行，制造 1:N ----------
INSERT INTO biz.order_items (order_id, product_id, quantity, unit_price) VALUES
 (1001, 1, 2, 399.00),    -- 键盘 x2
 (1001, 2, 2, 149.00),    -- 鼠标 x2（优惠后）
 (1001, 9, 4,  25.00),
 (1002, 4, 1, 548.00),
 (1003, 6, 1, 5999.00),
 (1004, 4, 1, 899.00),
 (1005, 3, 2, 1299.00),
 (1005, 10, 4, 119.00),
 (1005, 8, 2, 238.00),
 (1006, 5, 1, 1599.00),
 (1006, 9, 1,  29.00),
 (1007, 8, 1, 249.00),
 (1008, 7, 1, 2999.00),
 (1008, 10, 1, 129.00),
 (1009, 2, 3, 149.00),
 (1009, 9, 11, 25.00),
 (1010, 6, 1, 5999.00),
 (1010, 3, 1, 1299.00),
 (1010, 1, 1, 300.00),
 (1011, 1, 1, 399.00),
 (1012, 3, 1, 1299.00),
 (1013, 4, 1, 899.00),
 (1013, 9, 5,  29.80),
 (1014, 7, 1, 2999.00),
 (1015, 10, 1, 129.00),
 (1015, 9, 1,  29.00);

-- ---------- 支付：部分订单分期，制造 orders 1:N payments ----------
INSERT INTO biz.payments (order_id, method, amount, paid_at) VALUES
 (1001, 'alipay',  699.00, DATE '2024-01-05'),
 (1001, 'wechat',  598.00, DATE '2024-01-05'),
 (1002, 'card',    548.00, DATE '2024-01-20'),
 (1005, 'alipay', 2298.00, DATE '2024-03-08'),
 (1005, 'card',   2000.00, DATE '2024-03-08'),
 (1006, 'wechat', 1628.00, DATE '2024-04-02'),
 (1008, 'alipay', 3128.00, DATE '2024-05-06'),
 (1009, 'wechat',  727.00, DATE '2024-06-15'),
 (1010, 'card',   4000.00, DATE '2024-07-01'),
 (1010, 'alipay', 3598.00, DATE '2024-07-01'),
 (1011, 'alipay',  399.00, DATE '2024-08-18'),
 (1013, 'wechat', 1048.00, DATE '2024-10-12'),
 (1014, 'card',   2999.00, DATE '2024-11-02'),
 (1015, 'alipay',  158.00, DATE '2024-12-21');
