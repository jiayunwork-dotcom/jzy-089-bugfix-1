"""运行期配置，全部来自环境变量，容器编排负责注入。"""
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://drag:drag@localhost:5432/dragdb",
)

# 单次查询硬上限（毫秒 / 行数），与只读事务一起构成执行侧护栏
STATEMENT_TIMEOUT_MS = int(os.environ.get("QUERY_STATEMENT_TIMEOUT_MS", "5000"))
MAX_ROWS = int(os.environ.get("QUERY_MAX_ROWS", "10000"))

HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8000"))
