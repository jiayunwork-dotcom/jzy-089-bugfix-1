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
`tests/test_expected_queries.py` 对关键结构做断言；
`tests/test_live_db.py` 把这些语句真正发给 Postgres 执行并核对结果数字。

表名一律按模型登记的模式写成全限定名（`"biz"."orders"`），不依赖
会话级 `search_path`；列引用里的 `"orders"."id"` 是相关名（Postgres 对
`"biz"."orders"` 的默认别名即 `orders`），无需也不应带模式。

## A. 单表无 JOIN + IN 参数化

拖入行：订单状态；数值：订单总额；筛选：下单渠道 属于 [web, app]

```sql
SELECT "orders"."status", SUM("orders"."total_amount") AS "m_order_total"
FROM "biz"."orders"
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
FROM "biz"."order_items"
LEFT JOIN "biz"."orders" ON "order_items"."order_id" = "orders"."id"
LEFT JOIN "biz"."customers" ON "orders"."customer_id" = "customers"."id"
GROUP BY 1
ORDER BY 1
LIMIT $1
```

## C. 一对多放大：品类 × 订单总额（订单被明细复制，必须去重）

```sql
SELECT b."dim_product_category", r1."m_order_total"
FROM (
  SELECT "products"."category" AS "dim_product_category"
  FROM "biz"."orders"
  LEFT JOIN "biz"."order_items" ON "orders"."id" = "order_items"."order_id"
  LEFT JOIN "biz"."products" ON "order_items"."product_id" = "products"."id"
  GROUP BY 1
) b
LEFT JOIN (
  SELECT "dim_product_category", SUM("__v_m_order_total") AS "m_order_total"
  FROM (
    SELECT DISTINCT "products"."category" AS "dim_product_category",
           "orders"."id" AS "__pk",
           "orders"."total_amount" AS "__v_m_order_total"
    FROM "biz"."orders"
    LEFT JOIN "biz"."order_items" ON "orders"."id" = "order_items"."order_id"
    LEFT JOIN "biz"."products" ON "order_items"."product_id" = "products"."id"
  ) __dedup
  GROUP BY 1
) r1 ON r1."dim_product_category" IS NOT DISTINCT FROM b."dim_product_category"
ORDER BY b."dim_product_category"
LIMIT $1
```

要点：`orders.total_amount` 在 JOIN 明细后被复制；内层先按
`(品类, orders.id, total_amount)` 去重，再在外层求和，结果与
"直接对订单表按品类汇总"一致。维度在派生表内一律显式起别名
（`AS "dim_product_category"`），子查询之外只按别名引用——裸投影的
输出列名随表达式形态变化，外层继续写 `"products"."category"` 会落空
（missing FROM-clause entry）。

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
FROM "biz"."orders"
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
FROM "biz"."orders"
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
SELECT "dim_product_category",
       SUM("__v_m_pending_amount") FILTER (WHERE "__f_m_pending_amount")
           AS "m_pending_amount"
FROM (
  SELECT DISTINCT "products"."category" AS "dim_product_category",
         "orders"."id" AS "__pk",
         "orders"."total_amount" AS "__v_m_pending_amount",
         (("orders"."status" = $1)) AS "__f_m_pending_amount"
  FROM "biz"."orders"
  LEFT JOIN "biz"."order_items" ON ...
  LEFT JOIN "biz"."products" ON ...
) __dedup
GROUP BY 1
-- params: ['pending', 200]
```

## G. 度量筛选 → HAVING + 数值区间参数化

拖入行：客户分层；数值：平均订单额；筛选：平均订单额 ∈ [100, 1000]

```sql
SELECT "customers"."segment", AVG("orders"."total_amount") AS "m_avg_order"
FROM "biz"."orders"
LEFT JOIN "biz"."customers" ON "orders"."customer_id" = "customers"."id"
GROUP BY 1
HAVING (AVG("orders"."total_amount") BETWEEN $1 AND $2)
ORDER BY 1
LIMIT $3
-- params: [100, 1000, 200]
```

## H. 一对多组合下的度量筛选 → 外层 WHERE

拖入行：商品品类；数值：订单总额；筛选：订单总额 > 15000。
去重形态下各分组的聚合值落在派生列上，度量筛选翻译成最外层
`WHERE`（NULL 组被剔除，与 HAVING 语义一致）；筛选值仍是绑定参数，
且每个占位参数都被语句引用：

```sql
SELECT b."dim_product_category", r1."m_order_total"
FROM ( /* 同用例 C 的骨架 */ ) b
LEFT JOIN ( /* 同用例 C 的去重派生表 */ ) r1
  ON r1."dim_product_category" IS NOT DISTINCT FROM b."dim_product_category"
WHERE (r1."m_order_total" > $1)
ORDER BY b."dim_product_category"
LIMIT $2
-- params: [15000, 200]
```

被筛选的度量没拖进数值区时，它作为"隐藏度量"一并编译（进骨架或
去重派生表参与计算），但不进入输出列。

## 期望结果（自动化核对）

`tests/test_live_db.py` 在样例数据上核对以下数字
（`DRAGQUERY_TEST_DSN` 指向 Postgres 时自动执行）：

| 查询 | 期望 |
| --- | --- |
| 全量订单总额 | 32274.00 |
| 已支付（paid）订单总额 | 23828.00（pending/cancelled 不计） |
| 大区 × 订单总额 | 华东 8047、华北 14496、华南 9324、西南 407 |
| 品类 × 订单总额（用例 C） | 外设 15618、家具 4123、电脑 19724、配件 12533 |
| 品类 × 订单总额 + 销量 | (15618, 13)、(4123, 4)、(19724, 4)、(12533, 31)，两列互不干扰 |
| 品类 × 订单总额、筛 > 15000（用例 H） | 只剩 外设、电脑 两行 |
| 支付方式 × 订单总额 | alipay 16878、card 15443、wechat 4700（无支付订单落入 NULL 组 8446） |
| 各品类订单总额之和 | 51998 ≠ 32274：一张订单可含多个品类，该列不可加总（界面有提示） |
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
