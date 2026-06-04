# -*- coding: utf-8 -*-
"""
DbStress — 压测编排器。

实现 SETUP → WARMUP → STRESS → REPORT → CLEANUP 完整流程。
使用批次并发模型：每轮发出 concurrency 条命令到不同 threadID，
全部回调后收集指标再发下一轮，直到施压时长结束。
"""

import time
import KBEngine
from KBEDebug import INFO_MSG, ERROR_MSG, WARNING_MSG, DEBUG_MSG

from config import (
    TABLE_NAME,
    THREAD_ID_BASE,
    COMMAND_TIMEOUT_SEC,
    REPORT_DIR,
    DEFAULT_DURATION,
    DEFAULT_CONCURRENCY,
    DEFAULT_PRELOAD_ROWS,
    DEFAULT_WARMUP_SEC,
    DEFAULT_OP_WEIGHTS,
)
import sql_builder
from scenario import Scenario
from metrics import MetricsCollector, generate_report


# ============================================================
#  批量预填参数
# ============================================================

BULK_BATCH_SIZE = 200  # 每批 INSERT 的行数（MySQL/PostgreSQL 用 bulk insert）
SETUP_BATCH_CONCURRENCY = 4  # 预填时使用的并发数


# ============================================================
#  StressRunner
# ============================================================

class StressRunner(object):
    """
    压测编排器，每个 run() 调用创建一个新实例。

    同一时间只允许一个实例运行（通过类级别的 _running 标记防重入）。
    """

    _running = False

    def __init__(self, db_type, db_interface, duration, concurrency,
                 preload_rows, op_weights):
        self.db_type = db_type
        self.db_interface = db_interface
        self.duration = int(duration)
        self.concurrency = int(concurrency)
        self.preload_rows = int(preload_rows)
        self.op_weights = dict(op_weights)

        self._builder = sql_builder.get_builder(db_type)
        self._scenario = None  # 在 setup 完成后创建
        self._metrics = MetricsCollector()

        # 预填阶段
        self._preload_done = 0
        self._preload_total = self.preload_rows
        self._preload_pending = 0
        self._preload_start_time = 0.0

        # 施压阶段
        self._stress_start_time = 0.0
        self._batch_seq = 0
        self._batch_pending = 0
        self._batch_start_time = 0.0
        self._stop_requested = False

        # 报告路径
        self._report_filepath = self._make_report_path()

    # ================================================================
    #  入口
    # ================================================================

    def start(self):
        """启动压测流程。"""
        if StressRunner._running:
            WARNING_MSG("[DbStress] 已有压测在运行中，本次调用被忽略。")
            return

        StressRunner._running = True
        INFO_MSG("[DbStress] ========================================")
        INFO_MSG("[DbStress] 开始压测: type=%s, interface=%s, concurrency=%d, duration=%ds, preload=%d" % (
            self.db_type, self.db_interface, self.concurrency, self.duration, self.preload_rows))
        INFO_MSG("[DbStress] 报告路径: %s" % self._report_filepath)
        INFO_MSG("[DbStress] ========================================")

        self._start_setup()

    def _finish(self):
        """压测结束，释放标记。"""
        StressRunner._running = False
        INFO_MSG("[DbStress] 压测全部完成。")

    # ================================================================
    #  Phase 1: SETUP — 建表 + 预填数据
    # ================================================================

    def _start_setup(self):
        INFO_MSG("[DbStress] [SETUP] 创建压测表/集合 ...")
        cmd = self._builder.create_table()
        KBEngine.executeRawDatabaseCommand(
            cmd, self._on_create_table_done, THREAD_ID_BASE, self.db_interface)

    def _on_create_table_done(self, result, rows, insertid, error):
        if error:
            ERROR_MSG("[DbStress] [SETUP] 建表失败: %s" % error)
            self._finish()
            return
        INFO_MSG("[DbStress] [SETUP] 建表成功。")

        # 额外索引
        indexes = self._builder.create_indexes()
        if indexes:
            self._create_indexes(indexes, 0)
        else:
            self._verify_table_exists()

    def _create_indexes(self, indexes, idx):
        if idx >= len(indexes):
            self._verify_table_exists()
            return
        cmd = indexes[idx]
        DEBUG_MSG("[DbStress] [SETUP] 创建索引: %s" % cmd)
        KBEngine.executeRawDatabaseCommand(
            cmd,
            lambda r, rows, insid, err, i=idx: self._on_index_done(r, rows, insid, err, indexes, i),
            THREAD_ID_BASE, self.db_interface)

    def _on_index_done(self, result, rows, insertid, error, indexes, idx):
        if error:
            WARNING_MSG("[DbStress] [SETUP] 索引创建失败（非致命）: %s" % error)
        self._create_indexes(indexes, idx + 1)

    def _verify_table_exists(self):
        cmd = self._builder.verify_table_exists()
        KBEngine.executeRawDatabaseCommand(
            cmd, self._on_verify_table, THREAD_ID_BASE, self.db_interface)

    def _on_verify_table(self, result, rows, insertid, error):
        if error:
            ERROR_MSG("[DbStress] [SETUP] 验证表存在失败: %s" % error)
            self._finish()
            return
        INFO_MSG("[DbStress] [SETUP] 表/集合验证通过。开始预填 %d 行数据 ..." % self.preload_rows)

        self._preload_done = 0
        self._preload_pending = 0
        self._preload_start_time = time.time()

        # 根据 builder 是否支持 bulk insert 选择预填策略
        if self._builder.bulk_insert_prefix():
            self._preload_bulk()
        else:
            self._preload_single()

    # ---- 预填：批量 INSERT（MySQL / PostgreSQL） ----

    def _preload_bulk(self):
        """使用批量 INSERT 语句预填。"""
        while self._preload_done < self.preload_rows:
            batch_size = min(BULK_BATCH_SIZE, self.preload_rows - self._preload_done)
            prefix = self._builder.bulk_insert_prefix()
            values = []
            for i in range(batch_size):
                row_id = self._preload_done + i + 1
                name = sql_builder.make_row_name(row_id)
                category = sql_builder.make_category(row_id)
                score = 0.0
                payload = ""
                created_at = int(time.time())
                values.append(
                    self._builder.bulk_insert_values(
                        row_id, name, category, score, payload, created_at))
            cmd = prefix + ", ".join(values)
            self._preload_pending += 1
            KBEngine.executeRawDatabaseCommand(
                cmd,
                lambda r, rows, insid, err, n=batch_size: self._on_preload_batch(r, rows, insid, err, n),
                THREAD_ID_BASE, self.db_interface)
            self._preload_done += batch_size

    def _on_preload_batch(self, result, rows, insertid, error, batch_size):
        self._preload_pending -= 1
        if error:
            ERROR_MSG("[DbStress] [SETUP] 预填批次失败: %s" % error)
            self._finish()
            return
        if self._preload_pending == 0 and self._preload_done >= self.preload_rows:
            elapsed = time.time() - self._preload_start_time
            INFO_MSG("[DbStress] [SETUP] 预填完成: %d 行, 耗时 %.1fs" % (self.preload_rows, elapsed))
            self._start_warmup()

    # ---- 预填：单行 INSERT（MongoDB） ----

    def _preload_single(self):
        """使用单行 INSERT 逐条预填（MongoDB 等）。"""
        # 使用少量并发加速
        for i in range(min(SETUP_BATCH_CONCURRENCY, self.preload_rows)):
            self._preload_pending += 1
            self._emit_single_preload()

    def _emit_single_preload(self):
        if self._preload_done >= self.preload_rows:
            return

        row_id = self._preload_done + 1
        self._preload_done += 1
        name = sql_builder.make_row_name(row_id)
        category = sql_builder.make_category(row_id)
        cmd = self._builder.insert_one(row_id, name, category, 0.0, "", int(time.time()))
        thread_id = THREAD_ID_BASE + (row_id % self.concurrency)
        KBEngine.executeRawDatabaseCommand(
            cmd, self._on_single_preload_done, thread_id, self.db_interface)

    def _on_single_preload_done(self, result, rows, insertid, error):
        self._preload_pending -= 1
        if error:
            ERROR_MSG("[DbStress] [SETUP] 预填单行失败: %s" % error)
            self._finish()
            return

        # 继续发射直到全部完成
        if self._preload_done < self.preload_rows:
            self._preload_pending += 1
            self._emit_single_preload()

        if self._preload_pending == 0 and self._preload_done >= self.preload_rows:
            elapsed = time.time() - self._preload_start_time
            INFO_MSG("[DbStress] [SETUP] 预填完成: %d 行, 耗时 %.1fs" % (self.preload_rows, elapsed))
            self._start_warmup()

    # ================================================================
    #  Phase 2: WARMUP — 预热
    # ================================================================

    def _start_warmup(self):
        warmup_sec = DEFAULT_WARMUP_SEC
        INFO_MSG("[DbStress] [WARMUP] 预热 %ds（不计入指标）..." % warmup_sec)

        self._scenario = Scenario(self._builder, self.op_weights, self.preload_rows)
        self._warmup_end_time = time.time() + warmup_sec
        self._warmup_pending = 0

        # 低并发预热
        warmup_concurrency = max(1, self.concurrency // 4)
        for _ in range(warmup_concurrency):
            self._warmup_pending += 1
            self._emit_warmup_op()

    def _emit_warmup_op(self):
        op = self._scenario.next_op()
        thread_id = THREAD_ID_BASE + (self._warmup_pending % self.concurrency)
        KBEngine.executeRawDatabaseCommand(
            op.command,
            lambda r, rows, insid, err: self._on_warmup_done(r, rows, insid, err),
            thread_id, self.db_interface)

    def _on_warmup_done(self, result, rows, insertid, error):
        self._warmup_pending -= 1

        if time.time() < self._warmup_end_time:
            # 保持 in-flight 数量
            self._warmup_pending += 1
            self._emit_warmup_op()
        elif self._warmup_pending <= 0:
            INFO_MSG("[DbStress] [WARMUP] 预热完成，开始施压。")
            self._start_stress()

    # ================================================================
    #  Phase 3: STRESS — 正式施压
    # ================================================================

    def _start_stress(self):
        self._metrics.start()
        self._stress_start_time = time.time()
        self._stop_requested = False
        self._batch_seq = 0
        self._batch_pending = 0

        INFO_MSG("[DbStress] [STRESS] 开始施压，并发=%d，时长=%ds ..." % (
            self.concurrency, self.duration))
        self._emit_stress_batch()

    def _emit_stress_batch(self):
        """发射一轮完整的批次。"""
        self._batch_seq += 1
        self._batch_pending = self.concurrency
        self._batch_start_time = time.time()

        for i in range(self.concurrency):
            op = self._scenario.next_op()
            thread_id = THREAD_ID_BASE + i
            start_us = int(time.time() * 1000000)
            KBEngine.executeRawDatabaseCommand(
                op.command,
                lambda r, rows, insid, err, op=op, start=start_us, tid=thread_id:
                    self._on_stress_op_done(r, rows, insid, err, op, start, tid),
                thread_id, self.db_interface)

    def _on_stress_op_done(self, result, rows, insertid, error, op, start_us, thread_id):
        end_us = int(time.time() * 1000000)
        latency_us = max(0, end_us - start_us)

        op_metrics = self._metrics.get_op(op.op_type)

        if error:
            op_metrics.record_failure()
            err_msg = str(error)[:40]
            self._metrics.record_error("db_error: %s" % err_msg)
            DEBUG_MSG("[DbStress] [STRESS] %s 失败: %s" % (op.op_type, error))
        else:
            op_metrics.record_success(latency_us)

        # 更新场景状态
        if op.op_type == "INSERT" and not error:
            self._scenario.record_insert()
        elif op.op_type == "DELETE" and not error:
            self._scenario.record_delete()
            # DELETE 后补偿 INSERT 防行池枯竭（不计入批次，不计指标）
            self._emit_compensate_insert(thread_id)

        self._batch_pending -= 1

        if self._batch_pending == 0:
            self._on_batch_done()

    def _on_batch_done(self):
        """一个批次全部完成。"""
        elapsed = time.time() - self._stress_start_time

        # 检查是否应该停止
        if elapsed >= self.duration or self._stop_requested:
            self._metrics.stop()
            INFO_MSG("[DbStress] [STRESS] 施压结束。总操作数=%d, TPS=%.1f" % (
                self._metrics.total_ops, self._metrics.overall_tps))
            self._start_report()
            return

        # 继续下一批次
        self._emit_stress_batch()

    def _emit_compensate_insert(self, thread_id):
        """
        DELETE 后补偿 INSERT，防止行池枯竭。

        不计入批次计数器，不记录指标，只保持场景 ID 范围同步。
        """
        cmd = self._scenario.build_compensate_insert()

        def _on_compensate(result, rows, insertid, error):
            if not error:
                self._scenario.record_insert()

        KBEngine.executeRawDatabaseCommand(
            cmd, _on_compensate, thread_id, self.db_interface)

    # ================================================================
    #  Phase 4: REPORT — 生成报告
    # ================================================================

    def _start_report(self):
        INFO_MSG("[DbStress] [REPORT] 生成压测报告 ...")

        # 统计最终行数
        cmd = self._builder.count_all()
        KBEngine.executeRawDatabaseCommand(
            cmd, self._on_count_done, THREAD_ID_BASE, self.db_interface)

    def _on_count_done(self, result, rows, insertid, error):
        final_count = "N/A"
        if not error and result:
            try:
                if self.db_type == "mongodb":
                    final_count = str(len(result or []))
                else:
                    final_count = str(_first_cell(result))
            except Exception:
                pass

        INFO_MSG("[DbStress] [REPORT] 最终表行数: %s" % final_count)

        report = generate_report(
            self._metrics,
            self.db_type,
            self.db_interface,
            self.concurrency,
            self.preload_rows,
            self.op_weights,
            self._report_filepath,
        )
        self._start_cleanup()

    # ================================================================
    #  Phase 5: CLEANUP — 清理
    # ================================================================

    def _start_cleanup(self):
        INFO_MSG("[DbStress] [CLEANUP] 清理压测数据 ...")
        cmd = self._builder.drop_table()
        KBEngine.executeRawDatabaseCommand(
            cmd, self._on_drop_table_done, THREAD_ID_BASE, self.db_interface)

    def _on_drop_table_done(self, result, rows, insertid, error):
        if error:
            WARNING_MSG("[DbStress] [CLEANUP] 删表失败: %s" % error)
        else:
            INFO_MSG("[DbStress] [CLEANUP] 压测表/集合已清理。")
        self._verify_cleanup()

    def _verify_cleanup(self):
        cmd = self._builder.verify_table_dropped()
        KBEngine.executeRawDatabaseCommand(
            cmd, self._on_verify_cleanup, THREAD_ID_BASE, self.db_interface)

    def _on_verify_cleanup(self, result, rows, insertid, error):
        if error:
            WARNING_MSG("[DbStress] [CLEANUP] 清理验证失败: %s" % error)
        else:
            INFO_MSG("[DbStress] [CLEANUP] 清理验证通过。")
        self._finish()

    # ================================================================
    #  辅助
    # ================================================================

    def _make_report_path(self):
        """生成报告文件路径: scripts/logs/dbstress_YYYYMMDD_HHMMSS.log"""
        import os
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        filename = "dbstress_%s.log" % timestamp
        # REPORT_DIR 相对于 project root
        return os.path.join(REPORT_DIR, filename)


# ============================================================
#  辅助函数
# ============================================================

def _first_cell(result):
    """提取结果集第一个单元格的值（用于 COUNT 等标量查询）。"""
    if not result or not result[0] or result[0][0] is None:
        return 0
    value = result[0][0]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value
