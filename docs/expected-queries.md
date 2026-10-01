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

下面 7 组是随测试固化的"拖拽意图 → 期望 SQL"。所有表名按模型登记的模式
全限定（样例为 `"biz".`），所有 `$n` 均为绑定参数，
`tests/test_expected_queries.py` 对关键结构做断言。

> 真实连库、按结果数字核对的用例在 `tests/test_execution_db.py`，
> 见 [`design-decisions.md`](design-decisions.md)。

## A. 单表无 JOIN + IN 参数化

拖入行：订单状态；数值：订单总额；筛选：下单渠道 属于 [web, app]

```sql
SELECT "biz"."orders"."status" AS "dim_order_status",
       SUM("biz"."orders"."total_amount") AS "m_order_total"
FROM "biz"."orders"
WHERE ("biz"."orders"."channel" IN ($1, $2))
GROUP BY 1
ORDER BY 1
LIMIT $3
-- params: ['web', 'app', 200]
```

## B. 多对一安全路径（明细→订单→客户），无放大

拖入行：客户大区；数值：销量

```sql
SELECT "biz"."customers"."region" AS "dim_customer_region",
       SUM("biz"."order_items"."quantity") AS "m_qty"
FROM "biz"."order_items"
LEFT JOIN "biz"."orders" ON "biz"."order_items"."order_id" = "biz"."orders"."id"
LEFT JOIN "biz"."customers" ON "biz"."orders"."customer_id" = "biz"."customers"."id"
GROUP BY 1
ORDER BY 1
LIMIT $1
```

## C. 一对多放大：品类 × 订单总额（订单被明细复制，必须去重）

```sql
SELECT b."dim_product_category", r1."m_order_total"
FROM (
  SELECT "biz"."products"."category" AS "dim_product_category"
  FROM "biz"."orders"
  LEFT JOIN "biz"."order_items" ON "biz"."orders"."id" = "biz"."order_items"."order_id"
  LEFT JOIN "biz"."products" ON "biz"."order_items"."product_id" = "biz"."products"."id"
  GROUP BY 1
) b
LEFT JOIN (
  SELECT "dim_product_category", SUM("__v_m_order_total") AS "m_order_total"
  FROM (
    SELECT DISTINCT "biz"."products"."category" AS "dim_product_category",
           "biz"."orders"."id" AS "__pk",
           "biz"."orders"."total_amount" AS "__v_m_order_total"
    FROM "biz"."orders"
    LEFT JOIN "biz"."order_items" ON "biz"."orders"."id" = "biz"."order_items"."order_id"
    LEFT JOIN "biz"."products" ON "biz"."order_items"."product_id" = "biz"."products"."id"
  ) __dedup
  GROUP BY 1
) r1 ON r1."dim_product_category" IS NOT DISTINCT FROM b."dim_product_category"
ORDER BY b."dim_product_category"
LIMIT $1
```

要点：`orders.total_amount` 在 JOIN 明细后被复制；内层先按
`(品类, orders.id, total_amount)` 去重，再在外层求和，结果与
"每张订单对每个涉及品类只计一次"一致。注意中层 `FROM __dedup` 后物理表名
已离开作用域，维度必须引用派生表输出别名 `"dim_product_category"`
（旧实现误写 `"products"."category"`，真跑报 missing FROM-clause entry）。

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
SELECT DATE_TRUNC($1, "biz"."orders"."order_date") AS "dim_order_year",
       COUNT(DISTINCT "biz"."orders"."id") AS "m_order_count"
FROM "biz"."orders"
WHERE ("biz"."orders"."status" = $2)
GROUP BY 1
ORDER BY 1
LIMIT $3
-- params: ['year', 'paid', 200]
```

对年份单元格点击下钻后——只在分组追加季度，WHERE 与度量原样保留：

```sql
SELECT DATE_TRUNC($1, "biz"."orders"."order_date") AS "dim_order_year",
       DATE_TRUNC($2, "biz"."orders"."order_date") AS "dim_order_quarter",
       COUNT(DISTINCT "biz"."orders"."id") AS "m_order_count"
FROM "biz"."orders"
WHERE ("biz"."orders"."status" = $3)
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
SELECT b."dim_product_category", r1."m_pending_amount"
FROM (
  SELECT "biz"."products"."category" AS "dim_product_category"
  FROM "biz"."orders"
  LEFT JOIN "biz"."order_items" ON ...
  LEFT JOIN "biz"."products" ON ...
  GROUP BY 1
) b
LEFT JOIN (
  SELECT "dim_product_category",
         SUM("__v_m_pending_amount") FILTER (WHERE "__f_m_pending_amount")
             AS "m_pending_amount"
  FROM (
    SELECT DISTINCT "biz"."products"."category" AS "dim_product_category",
           "biz"."orders"."id" AS "__pk",
           "biz"."orders"."total_amount" AS "__v_m_pending_amount",
           (("biz"."orders"."status" = $1)) AS "__f_m_pending_amount"
    FROM "biz"."orders"
    LEFT JOIN "biz"."order_items" ON ...
    LEFT JOIN "biz"."products" ON ...
  ) __dedup
  GROUP BY 1
) r1 ON r1."dim_product_category" IS NOT DISTINCT FROM b."dim_product_category"
ORDER BY b."dim_product_category"
LIMIT $2
-- params: ['pending', 200]
```

## G. 度量筛选 → 聚合后过滤（外层 WHERE 引用聚合列别名）

拖入行：客户分层；数值：平均订单额；筛选：平均订单额 ∈ [100, 1000]

不再用 `HAVING`，而是在聚合结果外包一层，对聚合列别名过滤。这样扁平与
一对多去重两条路径写法完全一致，且每个过滤参数都确实出现在最终语句里；
`ORDER BY/LIMIT` 只放在最外层，保证"先过滤、后截断"。

```sql
SELECT "dim_customer_segment", "m_avg_order"
FROM (
  SELECT "biz"."customers"."segment" AS "dim_customer_segment",
         AVG("biz"."orders"."total_amount") AS "m_avg_order"
  FROM "biz"."orders"
  LEFT JOIN "biz"."customers" ON "biz"."orders"."customer_id" = "biz"."customers"."id"
  GROUP BY 1
) __agg
WHERE ("m_avg_order" BETWEEN $1 AND $2)
ORDER BY 1
LIMIT $3
-- params: [100, 1000, 200]
```

## 期望结果（人工核对用）

在样例数据上，几条关键查询的数值（由 `tests/test_execution_db.py` 自动核对）：

| 查询 | 期望 |
| --- | --- |
| 客户大区 × 订单总额 | 华东 8047、华北 14496、华南 9324、西南 407 |
| 商品品类 × 订单总额（去重，每订单每品类计一次） | 外设 15618、家具 4123、电脑 19724、配件 12533 |
| 商品品类 × 销量（多侧原生，可加） | 外设 13、家具 4、电脑 4、配件 31（合计 52） |
| 支付方式 × 订单总额 | alipay 16878、card 15443、wechat 4700（另有未支付 NULL 组 8446） |
| 品类 × 订单总额，筛订单总额 > 15000 | 只剩 外设 15618、电脑 19724 |
| 全量订单总额 | 32274.00 |
| 品类口径订单总额之和 | **51998.00（≠32274，多品类订单跨品类重复归因）** |
| 全量支付金额合计 | 23828.00 |
| 支付方式 × 销量 + 支付金额（用例 D） | 两列各自正确，不因明细×支付交叉而翻倍 |

> 品类合计 51998 > 全量订单总额 32274 是预期的**归因口径**，不是错误，也
> 不可用于对账；界面在命中一对多去重时会给出提示。解释与取舍见
> [`design-decisions.md`](design-decisions.md)。

容器启动后可在查询面板直接拖出上述组合核对，或：

```bash
curl -s localhost:8000/api/query/explain -H 'content-type: application/json' -d '{
  "rows":[{"dimension":"dim_product_category"}],
  "measures":["m_order_total"]
}'
```
