# DragQuery · 拖拽即生成查询

一个聚焦"拖拽即生成查询"的自助数据查询工具：不懂 SQL 的分析师在浏览器里把
**维度 / 度量 / 计算字段**拖进「行 / 列 / 数值 / 筛选」四个区域，后端把这套
拖拽意图翻译成**参数化、只读、JOIN 正确、防一对多重复计数**的 SQL，在
Postgres 上执行并把结果渲染成可排序、可沿层级下钻的表格。

它不是一套企业报表平台——没有仪表盘市场、没有图表库、没有调度订阅，
全部能力都围绕那台 SQL 翻译器做深。

## 目录结构

```
.
├── docker-compose.yml          # db(postgres:16-alpine) + backend(FastAPI) + frontend(nginx 静态托管)
├── db/init/01_sample_schema.sql# 随库自带的样例业务库（客户/商品/订单/明细/支付 + 外键）
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             # FastAPI 入口（lifespan 建池 / 模型存储）
│   │   ├── config.py           # 环境配置（DSN / 超时 / 行数上限）
│   │   ├── db.py               # asyncpg 连接池
│   │   ├── modeling/           # ★ 数据建模：schema / loader / catalog / validator / store
│   │   │   ├── schema.py       #   表/关联(1:1,1:N,N:1,N:N)/维度/层级/度量/计算字段
│   │   │   ├── sample_model.json
│   │   │   ├── catalog.py      #   表达式作用域、计算字段预编译与表依赖推导
│   │   │   ├── validator.py    #   保存模型前的整模型静态校验
│   │   │   └── store.py        #   meta.model_definition(JSONB) 持久化
│   │   ├── expressions/        # ★ 表达式白名单沙箱
│   │   │   ├── functions.py    #   安全函数白名单（IF/ROUND/UPPER/CONCAT/DATE_TRUNC…）
│   │   │   └── sandbox.py      #   手写 tokenizer+Pratt 解析，字面量全部参数化，错误带行列
│   │   ├── sqlgen/             # ★ SQL 生成内核（与建模/路由严格分文件）
│   │   │   ├── graph.py        #   关联图 + BFS 生成树（覆盖全部引用表、每表只连一次）
│   │   │   ├── spec.py         #   拖拽意图 QuerySpec + apply_drill 下钻
│   │   │   └── generator.py    #   翻译器：JOIN/GROUP BY/聚合/WHERE/HAVING/去重派生表
│   │   ├── execution/          # ★ 查询执行与结果整形
│   │   │   ├── guard.py        #   只读守卫：只放行单条 SELECT，拒绝写操作/DDL/多语句
│   │   │   └── executor.py     #   READ ONLY 事务 + statement_timeout + 行数硬上限
│   │   └── routes/             # HTTP 路由（model.py / query.py），不含 SQL 拼接逻辑
│   └── tests/                  # 102 个自动化测试（14 个需连库，见下）
└── frontend/                   # React + Vite + TS，构建后由 nginx 托管并反代 /api
    └── src/
        ├── components/query/   # 拖拽面板：FieldPalette/DropZone/FilterZone/ResultTable/SqlPreview
        └── components/model/   # 建模界面：表/关联/维度层级/度量/计算字段
```

## 一键启动

```bash
docker compose up -d --build
# 打开 http://localhost:8080
```

| 服务 | 默认地址 | 说明 |
| --- | --- | --- |
| 前端页面 | http://localhost:8080 | nginx 托管构建产物，`/api` 反代后端 |
| 后端 API | http://localhost:8000 | FastAPI，`/docs` 为 OpenAPI 界面 |
| Postgres | localhost:55432 | 用户/库：drag/drag/dragdb，首次启动载入样例库 |

端口可用环境变量覆盖：`WEB_PORT / BACKEND_PORT / DB_PORT`。

首次启动时后端会把 `backend/app/modeling/sample_model.json` 写入
`meta.model_definition`；之后建模界面的修改持久化在该表，重置只需删掉
`pgdata` 卷：`docker compose down -v`。

## 本地开发

```bash
# 后端
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DATABASE_URL=postgresql://drag:drag@localhost:55432/dragdb \
  uvicorn app.main:app --reload

# 前端（dev server 自动把 /api 代理到 8000）
cd frontend
npm install && npm run dev
```

## 自动化测试

```bash
cd backend
pip install -r requirements.txt
python -m pytest                     # 88 passed, 14 skipped（连库用例）

# 连库执行用例（需要可连的 Postgres，compose 环境即 db 服务）：
DRAGQUERY_TEST_DSN=postgresql://drag:drag@localhost:55432/dragdb \
  python -m pytest                   # 102 passed
```

内核、沙箱、守卫是纯函数模块，不依赖数据库即可运行；
`tests/test_live_db.py` 需要 `DRAGQUERY_TEST_DSN` 指向一份 Postgres
（测试会自行灌入 `db/init/01_sample_schema.sql` 样例数据，每个用例
新建连接、不改任何会话设置），未设置时整组跳过。测试文件与守护规则：

| 文件 | 守住的规则 |
| --- | --- |
| `tests/test_sqlgen.py` | 单表无 JOIN；跨表覆盖且不重复连表；1:N 不重复计数；SQL 稳定；下钻只改分组；全部参数化 |
| `tests/test_expected_queries.py` | 随产品附带的 7 组"拖拽 → 期望 SQL"固定核对用例 |
| `tests/test_sql_syntax.py` | 所有典型形态生成的 SQL 通过 Postgres 方言解析 |
| `tests/test_live_db.py` | **真连库执行**：样例数字核对（品类/支付方式/大区 × 订单总额等）、度量筛选生效、占位参数一一对应、枚举下拉、下钻、模式以模型登记为准 |
| `tests/test_sandbox.py` | 白名单函数、禁系统/属性/下标调用、字面量参数化、错误位置 |
| `tests/test_guard.py` | 只允许 SELECT，拒绝 INSERT/UPDATE/DDL/多语句/COPY/SET… |
| `tests/test_http.py` | 路由、400 行为、表达式校验接口、模型存取 |

详见 [`docs/expected-queries.md`](docs/expected-queries.md)。

## SQL 内核关键设计

1. **JOIN 推导**：关联声明构成无向图；以首个度量表为根做 BFS 生成树，
   裁剪挂不到引用表集合的分支。单表查询不产生任何 JOIN，每张表在生成树中
   至多出现一次。
2. **一对多防放大**：沿生成树逐边判断基数。只要存在会复制某度量表行的边，
   该度量就改为 `SELECT DISTINCT 维度, 表主键, 度量基列 [, 过滤标志]`
   派生表，在外层对去重结果聚合；度量级过滤提升为布尔标志列，避免
   `FILTER` 引用穿透子查询作用域。多个"多侧分支"（如明细 + 支付）各自去重，
   互不相乘。
3. **下钻**：`apply_drill` 只在分组中追加层级的下一级维度，过滤与度量原样保留；
   点击单元格产生的父级取值过滤由前端作为普通等值筛选加入。
4. **参数化**：所有筛选字面量、表达式字面量、`LIMIT` 全部是 `$n` 绑定参数，
   SQL 文本里永不出现用户输入。
5. **纵深只读**：内核只产出 SELECT；执行前 `guard` 做关键字/多语句静态检查，
   执行在 `SET TRANSACTION READ ONLY` 中进行，再叠加 `statement_timeout`
   与行数上限。

## 两个架构决策（理由与代价）

### 决策一：模式定位 —— 在语句里写全限定名

生成的 SQL 一律把表名写成 `"模式"."表"`（取自模型里每张表登记的
`schema_name`），**不**在连接层设 `search_path`。

- **理由**：查询自包含，发到任何会话、任何连接池节点结果都一样；
  建模界面可以从任意模式导入表，多张表分属不同模式时全限定名仍然唯一确定
  （`search_path` 是有序列表，两个在路径中的模式若存在同名表，必有一个被
  遮蔽，且解析结果随路径顺序漂移，与"以模型登记为准"冲突）；SQL 预览里
  看到的就是实际执行的文本，可审计、可拿到库里直接复跑。
- **代价**：SQL 文本更长；`search_path` 方案本可以让语句更短，但它把
  "表属于哪个模式"从模型挪到了会话状态，正是本次故障（`relation "orders"
  does not exist`）的根源，故不采用。

### 决策二：一对多防重复计数 —— 按主键去重的派生表

放大命中的度量改走 `SELECT DISTINCT 维度…, 度量表主键, 度量基列` 派生表，
外层再按维度聚合。

- **理由**：维度可能位于度量表跨过一对多关联的另一侧（品类之于订单），
  此时"先在度量所在表的粒度聚合再挂回维度"不成立——订单到品类本身
  就是一对多，聚合粒度必须包含维度，退化回同一个去重问题；按
  `(维度, 主键)` 去重则对任意维度位置都成立，且天然支持"同一订单计入
  多个品类"的语义。度量级过滤提升为布尔标志列随 DISTINCT 一起去重，
  多度量、多多侧分支各占一个派生表，互不相乘。
- **代价**：内层 DISTINCT 要对" join 后 + 过滤后"的行集排序/哈希，
  数据量大时比一次平坦 GROUP BY 贵；骨架连接在每个派生表里各重复
  一次（优化器通常会各自裁剪，但语句更长）。这是为正确性付的确定
  性成本。

### 关于"各品类的订单总额加起来超过全部订单总额"

一张订单含多个品类的明细时，它的总额会分别计入每个相关品类——
样例数据里各品类订单总额合计 51998，大于全部订单总额 32274。
**这是正确语义**：每一行回答的都是"该品类涉及到的订单总额是多少"，
行与行之间维度值重叠，这一列天然不可加总（要可加总的口径请用
「销售额」度量，它按明细行求和、各品类之和等于总计）。因为这一点
反直觉，结果表格在检测到一对多去重（`fanout_tables` 非空）时会
常驻一行提示，说明"各组之和可能大于总计"。
