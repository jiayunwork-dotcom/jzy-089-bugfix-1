# 样例模型与预期查询核对

样例业务库（`db/init/01_sample_schema.sql`）含 5 张表、4 条外键关联：

```
customers 1 ──< orders 1 ──< order_items >── 1 products
                     │ 1
                     └──< payments
```

语义模型（`backend/app/modeling/sample_model.json`）在其上声明：

- **层级**：地区（大区→省→市）、商品（品类→品牌→商品）、时间（年→季→月→日）
- **度量**：订单总额、订单数（去重）、平均订单额、销量、销售额（表达式）、
  支付金额、待支付订单额（带度量级过滤）
- **计算字段**：明细金额、单件毛利、毛利额（连接 products.cost）、
  毛利率（IF 条件判断 + 跨表字段）、客户标签（CONCAT/UPPER）

下面 7 组是随测试固化的"拖拽意图 → 期望 SQL"。所有 `$n` 均为绑定参数，
`tests/test_expected_queries.py` 对关键结构做断言。

## A. 单表无 JOIN + IN 参数化

拖入行：订单状态；数值：订单总额；筛选：下单渠道 属于 [web, app]

```sql
SELECT "orders"."status", SUM("orders"."total_amount") AS "m_order_total"
FROM "orders"
WHERE ("orders"."channel" IN ($1, $2))
GROUP BY 1
ORDER BY 1
LIMIT $3
-- params: ['web', 'app', 200]
```

## B. 多对一安全路径（明细→订单→客户），无放大

拖入行：客户大区；数值：销量

```sql
SELECT "customers"."region", SUM("order_items"."quantity") AS "m_qty"
FROM "order_items"
LEFT JOIN "orders" ON "order_items"."order_id" = "orders"."id"
LEFT JOIN "customers" ON "orders"."customer_id" = "customers"."id"
GROUP BY 1
ORDER BY 1
LIMIT $1
```

## C. 一对多放大：品类 × 订单总额（订单被明细复制，必须去重）

```sql
SELECT b."dim_product_category", r1."m_order_total"
FROM (
  SELECT "products"."category"
  FROM "orders"
  LEFT JOIN "order_items" ON "orders"."id" = "order_items"."order_id"
  LEFT JOIN "products" ON "order_items"."product_id" = "products"."id"
  GROUP BY 1
) b
LEFT JOIN (
  SELECT "products"."category", SUM("__v_m_order_total") AS "m_order_total"
  FROM (
    SELECT DISTINCT "products"."category",
           "orders"."id" AS "__pk",
           "orders"."total_amount" AS "__v_m_order_total"
    FROM "orders"
    LEFT JOIN "order_items" ON "orders"."id" = "order_items"."order_id"
    LEFT JOIN "products" ON "order_items"."product_id" = "products"."id"
  ) __dedup
  GROUP BY 1
) r1 ON r1."dim_product_category" IS NOT DISTINCT FROM b."dim_product_category"
ORDER BY b."dim_product_category"
LIMIT $1
```

要点：`orders.total_amount` 在 JOIN 明细后被复制；内层先按
`(品类, orders.id, total_amount)` 去重，再在外层求和，结果与
"直接对订单表按品类汇总"一致。

## D. 两个"多侧分支"：支付方式 × 销量 + 支付金额

明细与支付都是订单的多侧，直接连会让两侧交叉相乘。两个度量各走一个去重派生表
`r1` / `r2`，互不放大：

```sql
SELECT b."dim_payment_method", r1."m_qty", r2."m_payment_amount"
FROM ( /* 维度骨架：order_items -> orders -> payments，仅取分组键 */ ) b
LEFT JOIN ( /* SELECT DISTINCT method, order_items.id, quantity 后 SUM */ ) r1
  ON r1."dim_payment_method" IS NOT DISTINCT FROM b."dim_payment_method"
LEFT JOIN ( /* SELECT DISTINCT method, payments.id, amount 后 SUM */ ) r2
  ON r2."dim_payment_method" IS NOT DISTINCT FROM b."dim_payment_method"
ORDER BY b."dim_payment_method"
LIMIT $1
```

完整 SQL 可在前端「查看生成的 SQL」或运行
`python -m pytest tests/test_expected_queries.py::test_case_d_two_many_branches -s`
核对；测试断言：`fanout_tables == {order_items, payments}`、
`SELECT DISTINCT` 恰好出现两次、外层取 `r1.m_qty` 与 `r2.m_payment_amount`。

## E. 时间层级下钻（年 → 年+季），过滤与度量不变

下钻前（行：下单年份；数值：订单数；筛选：状态 = paid）：

```sql
SELECT DATE_TRUNC($1, "orders"."order_date"),
       COUNT(DISTINCT "orders"."id") AS "m_order_count"
FROM "orders"
WHERE ("orders"."status" = $2)
GROUP BY 1
ORDER BY 1
LIMIT $3
-- params: ['year', 'paid', 200]
```

对年份单元格点击下钻后——只在分组追加季度，WHERE 与度量原样保留：

```sql
SELECT DATE_TRUNC($1, "orders"."order_date"),
       DATE_TRUNC($2, "orders"."order_date"),
       COUNT(DISTINCT "orders"."id") AS "m_order_count"
FROM "orders"
WHERE ("orders"."status" = $3)
GROUP BY 1, 2
ORDER BY 1, 2
LIMIT $4
-- params: ['year', 'quarter', 'paid', 200]
```

（点击具体某一年时，前端还会追加 `year = 该年` 的等值筛选；那是普通筛选，
不属于 `apply_drill` 的职责。）

## F. 一对多场景下的度量级过滤

待支付订单额（`SUM(total_amount) FILTER (WHERE status='pending')`）
按品类汇总：过滤条件被提升为布尔标志列带进 DISTINCT 内层，外层只引用派生表列。

```sql
SELECT "products"."category",
       SUM("__v_m_pending_amount") FILTER (WHERE "__f_m_pending_amount")
           AS "m_pending_amount"
FROM (
  SELECT DISTINCT "products"."category",
         "orders"."id" AS "__pk",
         "orders"."total_amount" AS "__v_m_pending_amount",
         (("orders"."status" = $1)) AS "__f_m_pending_amount"
  FROM "orders"
  LEFT JOIN "order_items" ON ...
  LEFT JOIN "products" ON ...
) __dedup
GROUP BY 1
-- params: ['pending', 200]
```

## G. 度量筛选 → HAVING + 数值区间参数化

拖入行：客户分层；数值：平均订单额；筛选：平均订单额 ∈ [100, 1000]

```sql
SELECT "customers"."segment", AVG("orders"."total_amount") AS "m_avg_order"
FROM "orders"
LEFT JOIN "customers" ON "orders"."customer_id" = "customers"."id"
GROUP BY 1
HAVING (AVG("orders"."total_amount") BETWEEN $1 AND $2)
ORDER BY 1
LIMIT $3
-- params: [100, 1000, 200]
```

## 期望结果（人工核对用）

在样例数据上，几条关键查询的数值：

| 查询 | 期望 |
| --- | --- |
| 全量订单总额 | 32274.00 |
| 已支付（paid）订单总额 | 23828.00（pending/cancelled 不计） |
| 按品类汇总订单总额之和 | 32274.00（用例 C 去重后与单表一致，不被明细放大） |
| 全量支付金额合计 | 23828.00 |
| 销量合计（quantity 求和） | 52 |
| 支付方式 × 销量 + 支付金额（用例 D） | 两列各自正确，不因明细×支付交叉而翻倍 |

容器启动后可在查询面板直接拖出上述组合核对，或：

```bash
curl -s localhost:8000/api/query/explain -H 'content-type: application/json' -d '{
  "rows":[{"dimension":"dim_product_category"}],
  "measures":["m_order_total"]
}'
```
