# -*- coding: utf-8 -*-
"""
DbStress — 操作调度器。

根据权重随机选择操作类型，生成具体命令参数。
压测期间不断从调度器取下一步操作，模拟真实 OLTP 混合负载。
"""

import random
import hashlib
import time

import sql_builder


# ============================================================
#  Operation
# ============================================================

class Operation(object):
    """
    一次压测操作的描述。

    Attributes:
        op_type: 操作类型字符串，如 "POINT_SELECT"。
        command: 要传给 executeRawDatabaseCommand 的命令字符串。
        is_write: 是否为写操作（用于计数）。
    """

    __slots__ = ("op_type", "command", "is_write")

    def __init__(self, op_type, command, is_write=False):
        self.op_type = op_type
        self.command = command
        self.is_write = is_write


# ============================================================
#  Scenario — 加权操作调度器
# ============================================================

class Scenario(object):
    """
    根据操作权重随机生成压测操作。

    使用累积权重算法，O(log N) 查找。
    生成的命令参数在有效 ID 范围内随机变化，模拟真实负载分布。
    """

    def __init__(self, builder, op_weights, preload_rows):
        """
        Args:
            builder: sql_builder 返回的 Builder 实例。
            op_weights: dict，如 {"INSERT":10, "POINT_SELECT":40, ...}。
            preload_rows: setup 阶段预填的总行数，用于 ID 范围。
        """
        self._builder = builder
        self._preload_rows = int(preload_rows)
        self._insert_counter = self._preload_rows  # INSERT 时递增
        self._max_id = self._preload_rows           # 当前最大有效 ID

        # 构建累积权重
        self._op_types = []
        self._cum_weights = []
        total = 0
        for op_type, weight in op_weights.items():
            if weight <= 0:
                continue
            total += weight
            self._op_types.append(op_type)
            self._cum_weights.append(total)

        if not self._op_types:
            raise ValueError("DbStress.Scenario: 操作权重为空，至少配置一种操作。")

        self._total_weight = total
        self._write_ops = {"INSERT", "UPDATE", "DELETE"}

    # ---- 公共接口 ----

    def next_op(self):
        """
        生成下一次操作。

        Returns:
            Operation 实例。
        """
        op_type = self._pick_op_type()
        command = self._build_command(op_type)
        is_write = op_type in self._write_ops
        return Operation(op_type, command, is_write)

    def record_insert(self):
        """
        每次 INSERT 成功后调用，更新 ID 范围。

        这样后续 SELECT / UPDATE / DELETE 能命中新插入的行。
        """
        self._insert_counter += 1
        if self._insert_counter > self._max_id:
            self._max_id = self._insert_counter

    def record_delete(self):
        """DELETE 成功后调用（当前不缩减 max_id，因为概率性命中空行也合理）。"""
        pass

    def build_compensate_insert(self):
        """
        生成补偿 INSERT 命令（不参与权重随机，仅用于 DELETE 后行数补偿）。

        调用方负责在回调成功后调用 record_insert()。
        """
        return self._build_insert()

    # ---- 内部 ----

    def _pick_op_type(self):
        """按权重随机选择操作类型。"""
        r = random.randint(1, self._total_weight)
        for i, cum in enumerate(self._cum_weights):
            if r <= cum:
                return self._op_types[i]
        return self._op_types[-1]

    def _build_command(self, op_type):
        """根据操作类型生成具体命令。"""
        if op_type == "INSERT":
            return self._build_insert()
        elif op_type == "POINT_SELECT":
            return self._build_point_select()
        elif op_type == "RANGE_SELECT":
            return self._build_range_select()
        elif op_type == "UPDATE":
            return self._build_update()
        elif op_type == "DELETE":
            return self._build_delete()
        else:
            # 未知类型退回点查
            return self._build_point_select()

    def _random_id(self):
        """返回 [1, max_id] 范围内的随机 ID。"""
        if self._max_id <= 0:
            return 1
        return random.randint(1, self._max_id)

    def _build_insert(self):
        row_id = self._insert_counter + 1
        name = sql_builder.make_row_name(row_id)
        category = sql_builder.make_category(row_id)
        score = round(random.uniform(0.0, 100000.0), 2)
        payload = sql_builder.make_payload(256)
        created_at = int(time.time())
        return self._builder.insert_one(row_id, name, category, score, payload, created_at)

    def _build_point_select(self):
        row_id = self._random_id()
        return self._builder.point_select(row_id)

    def _build_range_select(self):
        lo = self._random_id()
        span = random.randint(10, 200)
        hi = lo + span
        return self._builder.range_select(lo, hi, 50)

    def _build_update(self):
        row_id = self._random_id()
        new_score = round(random.uniform(0.0, 100000.0), 2)
        new_payload = sql_builder.make_payload(128)
        return self._builder.update_one(row_id, new_score, new_payload)

    def _build_delete(self):
        # DELETE 比例小但有风险：频繁删空后 SELECT 大量 miss。
        # 这里每次 DELETE 后立即跟一次额外的 INSERT 补偿行数。
        # 补偿逻辑在 runner 层处理。
        row_id = self._random_id()
        return self._builder.delete_one(row_id)
