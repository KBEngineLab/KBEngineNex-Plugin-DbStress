# -*- coding: utf-8 -*-
"""
DbStress 压测插件 — 可配置参数默认值。
"""

# ---------- 表 / 集合命名 ----------
# 插件表统一使用 kbe_plugin_<插件>_<业务> 命名，避免和 assets 自身业务表冲突。
TABLE_NAME = "kbe_plugin_dbstress_data"

# ---------- 默认压测参数 ----------
DEFAULT_DB_TYPE = "mysql"        # mysql | pgsql | mongodb
DEFAULT_DB_INTERFACE = "default"  # kbengine.xml 中 databaseInterfaces 的名称
DEFAULT_DURATION = 60             # 施压时长（秒）
DEFAULT_CONCURRENCY = 16          # 并发数（= 分配的 threadID 数量）
DEFAULT_PRELOAD_ROWS = 10000      # setup 阶段预填数据行数
DEFAULT_WARMUP_SEC = 5            # 预热时长（秒）

# ---------- 操作权重（默认 OLTP 80:20 读写比） ----------
DEFAULT_OP_WEIGHTS = {
    "INSERT": 10,
    "POINT_SELECT": 40,
    "RANGE_SELECT": 20,
    "UPDATE": 20,
    "DELETE": 10,
}

# ---------- threadID 起始值 ----------
# 每个并发使用独立的 threadID，从该基准值开始递增。
THREAD_ID_BASE = 97001

# ---------- 命令超时 ----------
# 单条 executeRawDatabaseCommand 的最大等待秒数，超时计入 failure。
COMMAND_TIMEOUT_SEC = 30.0

# ---------- 报告输出路径 ----------
# KBE 服务进程的 cwd 为 scripts/，引擎日志也在此目录的 logs/ 下，
# 所以直接用 "logs" 即可与引擎日志并列。
REPORT_DIR = "logs"

# ---------- payload ----------
# INSERT 时附带的 payload 字段长度（字节），模拟真实游戏物品属性数据。
PAYLOAD_BYTES = 256
