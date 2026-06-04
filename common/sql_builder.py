# -*- coding: utf-8 -*-
"""
DbStress — 多数据库 SQL / 命令生成器。

支持 MySQL、PostgreSQL、MongoDB 三种后端的建表、索引、预填、
CRUD 操作命令生成。MongoDB 使用 KBE 的 collection.command(args) 格式。
"""

from config import TABLE_NAME


# ============================================================
#  工厂入口
# ============================================================

def get_builder(db_type):
    """
    返回对应数据库类型的 SqlBuilder 实例。

    Args:
        db_type: "mysql" | "pgsql" | "mongodb"

    Returns:
        _MysqlBuilder | _PgsqlBuilder | _MongoBuilder

    Raises:
        ValueError: 不支持的数据库类型。
    """
    db_type = (db_type or "").lower()
    if db_type == "mysql":
        return _MysqlBuilder()
    if db_type in ("pgsql", "postgresql", "postgres"):
        return _PgsqlBuilder()
    if db_type in ("mongodb", "mongo"):
        return _MongoBuilder()
    raise ValueError("DbStress: 不支持的数据库类型 '%s'，可选: mysql / pgsql / mongodb" % db_type)


# ============================================================
#  SQL Builder 基类
# ============================================================

class _BaseBuilder(object):
    """所有 Builder 的公共接口。"""

    def create_table(self):
        """返回建表命令。"""
        raise NotImplementedError

    def create_indexes(self):
        """返回额外的建索引命令列表（创建主键之外的索引）。"""
        return []

    def drop_table(self):
        """返回删表命令。"""
        raise NotImplementedError

    def verify_table_exists(self):
        """返回验证表存在的查询命令。"""
        raise NotImplementedError

    def verify_table_dropped(self):
        """返回验证表已删除的查询命令。"""
        raise NotImplementedError

    def insert_one(self, row_id, name, category, score, payload, created_at):
        """返回单行 INSERT 命令。"""
        raise NotImplementedError

    def point_select(self, row_id):
        """返回按主键点查命令。"""
        raise NotImplementedError

    def range_select(self, lo_id, hi_id, limit=50):
        """返回范围查询命令。"""
        raise NotImplementedError

    def update_one(self, row_id, new_score, new_payload):
        """返回按主键更新命令。"""
        raise NotImplementedError

    def delete_one(self, row_id):
        """返回按主键删除命令。"""
        raise NotImplementedError

    def count_all(self):
        """返回 COUNT(*) 命令。"""
        raise NotImplementedError

    def bulk_insert_prefix(self):
        """
        返回批量 INSERT 的前缀（不含 VALUES）。

        用于 setup 阶段的预填数据，子类可选实现以优化大批量写入。
        返回 None 表示使用单行 INSERT。
        """
        return None

    def bulk_insert_values(self, row_id, name, category, score, payload, created_at):
        """
        返回一条 VALUES 子句。

        当 bulk_insert_prefix() 返回非 None 时使用。
        调用方负责拼接 prefix + values + 后缀。
        """
        return None


# ============================================================
#  MySQL Builder
# ============================================================

class _MysqlBuilder(_BaseBuilder):

    def _t(self):
        """反引号包围的表名。"""
        return "`%s`" % TABLE_NAME

    def create_table(self):
        return (
            "CREATE TABLE IF NOT EXISTS %s ("
            "`id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
            "`name` VARCHAR(64) NOT NULL,"
            "`category` INT NOT NULL DEFAULT 0,"
            "`score` DOUBLE NOT NULL DEFAULT 0.0,"
            "`payload` TEXT NOT NULL,"
            "`created_at` BIGINT NOT NULL DEFAULT 0,"
            "PRIMARY KEY (`id`),"
            "KEY `idx_category` (`category`),"
            "KEY `idx_score` (`score`)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        ) % self._t()

    def drop_table(self):
        return "DROP TABLE IF EXISTS %s" % self._t()

    def verify_table_exists(self):
        return (
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='%s'"
        ) % TABLE_NAME

    def verify_table_dropped(self):
        return self.verify_table_exists()

    def insert_one(self, row_id, name, category, score, payload, created_at):
        return (
            "INSERT INTO %s (`name`, `category`, `score`, `payload`, `created_at`) "
            "VALUES ('%s', %d, %.6f, '%s', %d)"
        ) % (self._t(), name, category, score, payload, created_at)

    def point_select(self, row_id):
        return "SELECT `id`, `name`, `score`, `category`, `payload` FROM %s WHERE `id`=%d" % (
            self._t(), row_id)

    def range_select(self, lo_id, hi_id, limit=50):
        return (
            "SELECT `id`, `name`, `score`, `category` FROM %s "
            "WHERE `id` BETWEEN %d AND %d ORDER BY `id` LIMIT %d"
        ) % (self._t(), lo_id, hi_id, limit)

    def update_one(self, row_id, new_score, new_payload):
        return "UPDATE %s SET `score`=%.6f, `payload`='%s' WHERE `id`=%d" % (
            self._t(), new_score, new_payload, row_id)

    def delete_one(self, row_id):
        return "DELETE FROM %s WHERE `id`=%d" % (self._t(), row_id)

    def count_all(self):
        return "SELECT COUNT(*) FROM %s" % self._t()

    def bulk_insert_prefix(self):
        return "INSERT INTO %s (`name`, `category`, `score`, `payload`, `created_at`) VALUES " % self._t()

    def bulk_insert_values(self, row_id, name, category, score, payload, created_at):
        return "('%s', %d, %.6f, '%s', %d)" % (name, category, score, payload, created_at)


# ============================================================
#  PostgreSQL Builder
# ============================================================

class _PgsqlBuilder(_BaseBuilder):

    def _t(self):
        """双引号包围的表名。"""
        return '"%s"' % TABLE_NAME

    def create_table(self):
        return (
            "CREATE TABLE IF NOT EXISTS %s ("
            "id BIGSERIAL PRIMARY KEY,"
            "name VARCHAR(64) NOT NULL,"
            "category INTEGER NOT NULL DEFAULT 0,"
            "score DOUBLE PRECISION NOT NULL DEFAULT 0.0,"
            "payload TEXT NOT NULL,"
            "created_at BIGINT NOT NULL DEFAULT 0"
            ")"
        ) % self._t()

    def create_indexes(self):
        t = self._t()
        return [
            "CREATE INDEX IF NOT EXISTS idx_dbstress_category ON %s (category)" % t,
            "CREATE INDEX IF NOT EXISTS idx_dbstress_score ON %s (score)" % t,
        ]

    def drop_table(self):
        return "DROP TABLE IF EXISTS %s" % self._t()

    def verify_table_exists(self):
        return (
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='%s'"
        ) % TABLE_NAME

    def verify_table_dropped(self):
        return self.verify_table_exists()

    def insert_one(self, row_id, name, category, score, payload, created_at):
        return (
            "INSERT INTO %s (name, category, score, payload, created_at) "
            "VALUES ('%s', %d, %.6f, '%s', %d)"
        ) % (self._t(), name, category, score, payload, created_at)

    def point_select(self, row_id):
        return "SELECT id, name, score, category, payload FROM %s WHERE id=%d" % (
            self._t(), row_id)

    def range_select(self, lo_id, hi_id, limit=50):
        return (
            "SELECT id, name, score, category FROM %s "
            "WHERE id BETWEEN %d AND %d ORDER BY id LIMIT %d"
        ) % (self._t(), lo_id, hi_id, limit)

    def update_one(self, row_id, new_score, new_payload):
        return "UPDATE %s SET score=%.6f, payload='%s' WHERE id=%d" % (
            self._t(), new_score, new_payload, row_id)

    def delete_one(self, row_id):
        return "DELETE FROM %s WHERE id=%d" % (self._t(), row_id)

    def count_all(self):
        return "SELECT COUNT(*) FROM %s" % self._t()

    def bulk_insert_prefix(self):
        return "INSERT INTO %s (name, category, score, payload, created_at) VALUES " % self._t()

    def bulk_insert_values(self, row_id, name, category, score, payload, created_at):
        return "('%s', %d, %.6f, '%s', %d)" % (name, category, score, payload, created_at)


# ============================================================
#  MongoDB Builder
# ============================================================

class _MongoBuilder(_BaseBuilder):

    def _c(self):
        """集合名，直接使用 TABLE_NAME。"""
        return TABLE_NAME

    def create_table(self):
        # MongoDB 不需要显式建集合，建索引即可。
        return '%s.createIndex({"category":1}, {"name":"dbstress_category_idx"})' % self._c()

    def drop_table(self):
        # executeRawDatabaseCommand 黑名单默认关闭（blacklist enabled=false），
        # drop 命令可以正常执行。
        return '%s.drop()' % self._c()

    def verify_table_exists(self):
        # 通过空查询验证集合可访问。
        return '%s.find({}, {"_id":1}, 1)' % self._c()

    def verify_table_dropped(self):
        # 对已删除的集合执行 find 会返回空游标。
        return '%s.find({}, {"_id":1}, 1)' % self._c()

    def insert_one(self, row_id, name, category, score, payload, created_at):
        return (
            '%s.insert({"name":"%s","category":%d,"score":%.6f,"payload":"%s","created_at":%d})'
        ) % (self._c(), name, category, score, payload, created_at)

    def point_select(self, row_id):
        # MongoDB 没有自增 ID，用 name 字段模拟点查（setup 阶段 name 唯一）。
        return '%s.find({"name":"row_%d"}, {"name":1,"score":1,"category":1,"payload":1}, 1)' % (
            self._c(), row_id)

    def range_select(self, lo_id, hi_id, limit=50):
        # 用 category 字段模拟范围查询。
        return (
            '%s.find({"category":{"$gte":%d,"$lte":%d}}, {"name":1,"score":1,"category":1}, %d)'
        ) % (self._c(), lo_id, hi_id, limit)

    def update_one(self, row_id, new_score, new_payload):
        return (
            '%s.update({"name":"row_%d"}, {"$set":{"score":%.6f,"payload":"%s"}}, false, true)'
        ) % (self._c(), row_id, new_score, new_payload)

    def delete_one(self, row_id):
        return '%s.remove({"name":"row_%d"})' % (self._c(), row_id)

    def count_all(self):
        return '%s.find({}, {"_id":1})' % self._c()

    def bulk_insert_prefix(self):
        # MongoDB 没有 SQL 风格的批量 insert prefix，返回标记让调用方走单行。
        return None


# ============================================================
#  辅助函数
# ============================================================

def make_payload(size_bytes):
    """
    生成指定长度的伪随机 payload 字符串，模拟游戏物品的附加属性 JSON。

    Args:
        size_bytes: 目标字节数（近似值，中文占 3 字节按 UTF-8）。
    """
    import random
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    # 每字符 1 字节，直接按长度生成。
    payload = "".join(random.choice(chars) for _ in range(size_bytes))
    return payload


def make_row_name(row_id):
    """生成规范的行名，用于压测中标识行。"""
    return "row_%d" % row_id


def make_category(row_id, num_categories=10):
    """根据 row_id 生成分类编号（0 ~ num_categories-1）。"""
    return row_id % num_categories
