# DbStress — 数据库压测插件

KBE Nex 数据库压测插件，基于 `executeRawDatabaseCommand` 实现，覆盖增删改查全场景，支持 MySQL、PostgreSQL、MongoDB。

## 快速开始

### 导入

在 baseapp / cellapp 脚本中导入：

```python
from plugins.DbStress.common import DbStress
```

### MySQL

```python
# 默认参数：60s、16 并发、10000 行预填、OLTP 80:20 读写比
DbStress.run(dbType="mysql")

# 自定义参数
DbStress.run(
    dbType="mysql",
    duration=120,         # 施压 2 分钟
    concurrency=32,       # 32 并发
    preloadRows=100000,   # 预填 10 万行
)

# 自定义操作权重（读写比 50:50）
DbStress.run(
    dbType="mysql",
    opWeights={
        "INSERT": 25,
        "POINT_SELECT": 25,
        "RANGE_SELECT": 10,
        "UPDATE": 25,
        "DELETE": 15,
    },
)

# 指定数据库接口（kbengine.xml 中配置了多个接口时使用）
DbStress.run(dbType="mysql", dbInterface="mysql_ro")
```

### PostgreSQL

```python
# pgsql 是 postgresql / postgres 的简写，效果相同
DbStress.run(dbType="pgsql")

# 全参数
DbStress.run(
    dbType="pgsql",
    dbInterface="default",
    duration=60,
    concurrency=16,
    preloadRows=20000,
    opWeights={
        "INSERT": 10,
        "POINT_SELECT": 50,
        "RANGE_SELECT": 10,
        "UPDATE": 20,
        "DELETE": 10,
    },
)
```

> `dbType` 支持 `"pgsql"` / `"postgresql"` / `"postgres"`，三种写法等效。

### MongoDB

```python
# mongo 是 mongodb 的简写
DbStress.run(dbType="mongodb")

# 全参数
DbStress.run(
    dbType="mongodb",
    duration=60,
    concurrency=8,
    preloadRows=5000,
)
```

> `dbType` 支持 `"mongodb"` / `"mongo"`，两种写法等效。

### 不同场景的推荐参数

| 场景 | duration | concurrency | preloadRows | 说明 |
|------|----------|-------------|-------------|------|
| 冒烟测试 | 10 | 4 | 1000 | 快速验证连通性和基本性能 |
| 基准测试 | 60 | 16 | 10000 | 获取稳定 TPS/延迟基线 |
| 压力测试 | 300 | 64 | 100000 | 长时间高并发找瓶颈 |
| 容量测试 | 120 | 128 | 500000 | 大并发 + 大数据量测极限 |

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `dbType` | str | `"mysql"` | 数据库类型：`mysql` / `pgsql` / `mongodb` |
| `dbInterface` | str | `"default"` | kbengine.xml 中 databaseInterfaces 的名称 |
| `duration` | int | `60` | 正式施压时长（秒） |
| `concurrency` | int | `16` | 并发数（= threadID 数量） |
| `preloadRows` | int | `10000` | setup 阶段预填数据行数 |
| `opWeights` | dict | 见下方 | CRUD 操作权重 |

默认操作权重（OLTP 80:20 读写比）：

```python
{
    "INSERT": 10,
    "POINT_SELECT": 40,
    "RANGE_SELECT": 20,
    "UPDATE": 20,
    "DELETE": 10,
}
```

## concurrency 与 numConnections

压测的 `concurrency` 参数和引擎的 `numConnections` 是两个层级的概念，理解它们的关系是调优关键。

### numConnections（引擎层）

`kbengine.xml` → `<dbmgr>` → `<databaseInterfaces>` → `<default>` → `<numConnections>`：

```xml
<dbmgr>
    <databaseInterfaces>
        <default>
            <!-- ... -->
            <numConnections> 5 </numConnections>
        </default>
    </databaseInterfaces>
</dbmgr>
```

`numConnections` 控制 **dbmgr 进程为每个数据库接口创建的线程数**。每个线程持有独立的数据库连接（MySQL 连接、MongoDB client 等）。引擎启动日志可验证：

```
ThreadPool::createThreadPool: successfully(5), newThreadCount=5
```

### concurrency（压测层）

`DbStress.run(concurrency=N)` 控制**每轮同时发出多少条 `executeRawDatabaseCommand` 请求**。每个并发使用独立的 `threadID` 路由到 dbmgr 的不同线程。

### 关系

```
DbStress concurrency=16
        ↓
   16 条请求同时发出（不同 threadID）
        ↓
   dbmgr 线程池（numConnections=5）
        ↓
   5 条在跑，11 条在排队
```

**`concurrency` 超过 `numConnections` 时，多余的请求在 dbmgr 内部排队**，不会报错，但延迟会明显升高。

### 实测算例（同一 MySQL 环境）

| numConnections | concurrency | TPS | P50 | P95 | 说明 |
|---------------|-------------|-----|-----|-----|------|
| 5 | 4 | 122 | 33ms | 48ms | 轻载，管道开销主导 |
| 5 | 16 | 233 | 49ms | 75ms | 线程池饱和，排队明显 |
| 16 | 16 | 预计 500+ | 预计 <20ms | — | 消除排队瓶颈 |

### 建议

1. **先确认当前 `numConnections`**：看启动日志或 `kbengine.xml`
2. **`concurrency` 设为 `numConnections` 的 1~3 倍**：太小测不出排队，太大会让延迟失真
3. **如果 TPS 不随 concurrency 线性增长**：瓶颈在 `numConnections`，先加大线程池再重测

## 压测流程

```
SETUP → WARMUP → STRESS → REPORT → CLEANUP
```

| 阶段 | 说明 |
|------|------|
| SETUP | 创建压测表 `kbe_plugin_dbstress_data`、索引，预填基准数据 |
| WARMUP | 低并发预热（默认 5s），不计入指标 |
| STRESS | 按配置的并发度 + 时长正式施压 |
| REPORT | 生成报告，输出到 `scripts/logs/dbstress_YYYYMMDD_HHMMSS.log` |
| CLEANUP | 删除压测表/集合 |

## 压测表结构

```sql
CREATE TABLE kbe_plugin_dbstress_data (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name       VARCHAR(64) NOT NULL,
    category   INT NOT NULL DEFAULT 0,
    score      DOUBLE NOT NULL DEFAULT 0.0,
    payload    TEXT NOT NULL,
    created_at BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_category (category),
    KEY idx_score (score)
);
```

MongoDB 使用同名集合，PostgreSQL 使用 `BIGSERIAL` 自增主键。

## 报告示例

```
================================================================================
  DbStress Report
  DB Type:      mysql
  Interface:    default
  Concurrency:  16
  Preload:      10000 rows
  Weights:      DELETE=10  INSERT=10  POINT_SELECT=40  RANGE_SELECT=20  UPDATE=20
  Duration:     60.0s (stress phase)
================================================================================
  Total Ops:     24532
  Success:       24520 (99.95%)
  Failure:       12  (0.05%)
  Overall TPS:   408.7
  Avg Latency:   1.8ms
  Global P50:    1.2ms
  Global P95:    4.8ms
  Global P99:    8.2ms

  Per-Operation:
  Op              Count     TPS     avg     P50     P95     P99     max
  POINT_SELECT    14712   245.2    1.1ms   0.9ms   2.3ms   4.1ms   9.8ms
  RANGE_SELECT     4898    81.6    2.8ms   2.5ms   5.6ms   8.9ms  18.2ms
  INSERT           2451    40.9    2.3ms   2.1ms   4.8ms   7.2ms  15.3ms
  UPDATE           2451    40.9    1.8ms   1.6ms   3.9ms   6.5ms  12.1ms
  DELETE             20     0.3    1.5ms   1.4ms   2.8ms   4.3ms   8.5ms

  Latency Distribution (all ops):
  <= 0.1ms  : ##### 4.2% (1038 ops)
  <= 0.2ms  : ######## 7.8% (1912 ops)
  ...
```

## 注意事项

1. **同一时间只允许一个压测**：重复调用 `run()` 会被自动忽略。
2. **表名隔离**：使用 `kbe_plugin_dbstress_` 前缀，不与业务表冲突。
3. **MongoDB drop**：黑名单默认关闭，`collection.drop()` 可正常执行。若环境开启了黑名单，drop 会触发回调 error，不影响流程。
4. **回调模型**：`executeRawDatabaseCommand` 是异步回调模式，压测通过批次计数器保证顺序。
5. **报告落盘**：每次压测生成独立报告文件，不会覆盖历史报告。
