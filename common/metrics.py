# -*- coding: utf-8 -*-
"""
DbStress — 指标统计与报告输出。

使用分桶直方图收集延迟数据，支持 P50/P95/P99 百分位计算，
最终生成行业标准格式的压测报告并写入 scripts/logs/ 目录。
"""

import os
import time
from KBEDebug import INFO_MSG, ERROR_MSG


# ============================================================
#  延迟分桶（微秒）
# ============================================================

# 桶边界（单位：毫秒），覆盖 0.1ms ~ 10s
_LATENCY_BUCKET_BOUNDARIES_MS = [
    0.5, 1, 2, 5,
    10, 15, 20, 25, 30, 35, 40, 45, 50,
    75, 100, 200, 500,
    1000, 2000, 5000,
    10000,
]

# 转换为微秒
_LATENCY_BUCKET_BOUNDARIES_US = [int(b * 1000) for b in _LATENCY_BUCKET_BOUNDARIES_MS]


class LatencyHistogram(object):
    """
    分桶延迟直方图。

    每个桶记录落在 [prev_boundary, boundary) 区间内的请求次数。
    超过最大边界的请求计入 overflow 桶。
    """

    def __init__(self):
        self.buckets = [0] * len(_LATENCY_BUCKET_BOUNDARIES_US)
        self.overflow = 0
        self._count = 0
        self._sum_us = 0
        self._min_us = None
        self._max_us = None

    def record(self, latency_us):
        """记录一次延迟（单位：微秒）。"""
        self._count += 1
        self._sum_us += latency_us

        if self._min_us is None or latency_us < self._min_us:
            self._min_us = latency_us
        if self._max_us is None or latency_us > self._max_us:
            self._max_us = latency_us

        for i, boundary in enumerate(_LATENCY_BUCKET_BOUNDARIES_US):
            if latency_us < boundary:
                self.buckets[i] += 1
                return
        self.overflow += 1

    @property
    def count(self):
        return self._count

    @property
    def avg_us(self):
        if self._count == 0:
            return 0.0
        return self._sum_us / self._count

    @property
    def min_us(self):
        return self._min_us or 0

    @property
    def max_us(self):
        return self._max_us or 0

    def percentile(self, p):
        """
        计算第 p 百分位延迟（微秒）。

        Args:
            p: 0~100 的浮点数，如 50、95、99。

        Returns:
            微秒整数。
        """
        if self._count == 0:
            return 0

        target = int(self._count * p / 100.0)
        if target >= self._count:
            target = self._count - 1

        cumulative = 0
        prev_boundary = 0
        for i, boundary in enumerate(_LATENCY_BUCKET_BOUNDARIES_US):
            cumulative += self.buckets[i]
            if cumulative > target:
                # 线性插值
                count_in_bucket = self.buckets[i]
                if count_in_bucket == 0:
                    return boundary
                rank_in_bucket = target - (cumulative - count_in_bucket)
                ratio = float(rank_in_bucket) / count_in_bucket
                return int(prev_boundary + ratio * (boundary - prev_boundary))
            prev_boundary = boundary

        # 落在 overflow 桶
        return _LATENCY_BUCKET_BOUNDARIES_US[-1]

    def p50(self):
        return self.percentile(50)

    def p95(self):
        return self.percentile(95)

    def p99(self):
        return self.percentile(99)

    def distribution_bars(self):
        """
        返回延迟分布信息：每个桶的百分比和计数。

        Returns:
            list of (label, count, pct)
        """
        result = []
        prev = 0
        for i, boundary in enumerate(_LATENCY_BUCKET_BOUNDARIES_US):
            count = self.buckets[i]
            label = "<= %.1fms" % (boundary / 1000.0)
            result.append((label, count, _safe_pct(count, self._count)))
            prev = boundary
        if self.overflow > 0:
            result.append(
                ("> %.1fms" % (prev / 1000.0), self.overflow, _safe_pct(self.overflow, self._count))
            )
        return result


# ============================================================
#  Per-Operation 指标
# ============================================================

class OpMetrics(object):
    """单个操作类型的指标收集器。"""

    def __init__(self, op_name):
        self.op_name = op_name
        self.histogram = LatencyHistogram()
        self.success_count = 0
        self.failure_count = 0

    def record_success(self, latency_us):
        self.success_count += 1
        self.histogram.record(latency_us)

    def record_failure(self):
        self.failure_count += 1

    @property
    def total_count(self):
        return self.success_count + self.failure_count

    @property
    def error_rate(self):
        if self.total_count == 0:
            return 0.0
        return self.failure_count / float(self.total_count)


# ============================================================
#  MetricsCollector — 全局指标聚合
# ============================================================

class MetricsCollector(object):
    """压测全局指标收集器。"""

    def __init__(self):
        self._ops = {}          # op_name -> OpMetrics
        self._errors = {}       # error_msg -> count
        self._start_time = None
        self._end_time = None

    def get_op(self, op_name):
        """获取或创建某个操作类型的 OpMetrics。"""
        if op_name not in self._ops:
            self._ops[op_name] = OpMetrics(op_name)
        return self._ops[op_name]

    def record_error(self, error_msg):
        """记录错误类型计数。"""
        self._errors[error_msg] = self._errors.get(error_msg, 0) + 1

    def start(self):
        self._start_time = time.time()

    def stop(self):
        self._end_time = time.time()

    @property
    def duration_sec(self):
        if self._start_time is None or self._end_time is None:
            return 0.0
        return self._end_time - self._start_time

    @property
    def total_success(self):
        return sum(op.success_count for op in self._ops.values())

    @property
    def total_failure(self):
        return sum(op.failure_count for op in self._ops.values())

    @property
    def total_ops(self):
        return self.total_success + self.total_failure

    @property
    def overall_tps(self):
        dur = self.duration_sec
        if dur <= 0:
            return 0.0
        return self.total_ops / dur

    @property
    def overall_error_rate(self):
        if self.total_ops == 0:
            return 0.0
        return self.total_failure / float(self.total_ops)

    @property
    def sorted_ops(self):
        """按 success_count 降序排列的操作列表。"""
        return sorted(self._ops.values(), key=lambda m: -m.success_count)


# ============================================================
#  报告生成
# ============================================================

def generate_report(metrics, db_type, db_interface, concurrency,
                    preload_rows, op_weights, report_filepath):
    """
    生成压测报告字符串，同时写入文件和控制台。

    Args:
        metrics: MetricsCollector 实例。
        db_type: 数据库类型。
        db_interface: 数据库接口名。
        concurrency: 并发数。
        preload_rows: 预填行数。
        op_weights: 操作权重字典。
        report_filepath: 报告输出文件完整路径。

    Returns:
        报告文本字符串。
    """
    lines = []
    _append_header(lines, db_type, db_interface, concurrency,
                   preload_rows, op_weights, metrics)
    _append_summary(lines, metrics)
    _append_per_op(lines, metrics)
    _append_distribution(lines, metrics)
    _append_errors(lines, metrics)
    lines.append("=" * 80)

    report = "\n".join(lines)

    # 写入文件
    try:
        _ensure_dir(os.path.dirname(report_filepath))
        with open(report_filepath, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        INFO_MSG("[DbStress] 压测报告已写入: %s" % report_filepath)
    except Exception as e:
        ERROR_MSG("[DbStress] 写入报告失败: %s" % e)

    # 控制台精简版
    for line in lines:
        INFO_MSG(line)

    return report


def _append_header(lines, db_type, db_interface, concurrency,
                   preload_rows, op_weights, metrics):
    lines.append("=" * 80)
    lines.append("  DbStress Report")
    lines.append("  DB Type:      %s" % db_type)
    lines.append("  Interface:    %s" % db_interface)
    lines.append("  Concurrency:  %d" % concurrency)
    lines.append("  Preload:      %d rows" % preload_rows)
    weights_str = "  ".join("%s=%d" % (k, v) for k, v in sorted(op_weights.items()))
    lines.append("  Weights:      %s" % weights_str)
    lines.append("  Duration:     %.1fs (stress phase)" % metrics.duration_sec)
    lines.append("=" * 80)


def _append_summary(lines, metrics):
    lines.append("")
    lines.append("  Total Ops:     %d" % metrics.total_ops)
    lines.append("  Success:       %d (%.2f%%)" % (
        metrics.total_success, (1.0 - metrics.overall_error_rate) * 100))
    lines.append("  Failure:       %d (%.2f%%)" % (
        metrics.total_failure, metrics.overall_error_rate * 100))
    lines.append("  Overall TPS:   %.1f" % metrics.overall_tps)

    # 计算全局平均延迟
    total_count = sum(op.histogram.count for op in metrics.sorted_ops)
    if total_count > 0:
        total_sum = sum(op.histogram.count * op.histogram.avg_us for op in metrics.sorted_ops)
        global_avg_us = total_sum / total_count
        lines.append("  Avg Latency:   %.1fms" % (global_avg_us / 1000.0))

    # 全局百分位
    global_hist = _merge_histograms(metrics)
    if global_hist.count > 0:
        lines.append("  Global P50:    %.1fms" % (global_hist.p50() / 1000.0))
        lines.append("  Global P95:    %.1fms" % (global_hist.p95() / 1000.0))
        lines.append("  Global P99:    %.1fms" % (global_hist.p99() / 1000.0))


def _append_per_op(lines, metrics):
    lines.append("")
    lines.append("  Per-Operation:")
    header = "  %-14s %8s %8s %8s %8s %8s %8s %8s" % (
        "Op", "Count", "TPS", "avg", "P50", "P95", "P99", "max")
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for op in metrics.sorted_ops:
        h = op.histogram
        dur = metrics.duration_sec
        tps = op.total_count / dur if dur > 0 else 0.0
        avg_str = "%.1fms" % (h.avg_us / 1000.0) if h.count > 0 else "-"
        p50_str = "%.1fms" % (h.p50() / 1000.0) if h.count > 0 else "-"
        p95_str = "%.1fms" % (h.p95() / 1000.0) if h.count > 0 else "-"
        p99_str = "%.1fms" % (h.p99() / 1000.0) if h.count > 0 else "-"
        max_str = "%.1fms" % (h.max_us / 1000.0) if h.count > 0 else "-"
        row = "  %-14s %8d %8.1f %8s %8s %8s %8s %8s" % (
            op.op_name, op.total_count, tps, avg_str, p50_str, p95_str, p99_str, max_str)
        lines.append(row)


def _append_distribution(lines, metrics):
    global_hist = _merge_histograms(metrics)
    if global_hist.count == 0:
        return

    lines.append("")
    lines.append("  Latency Distribution (all ops):")
    bars = global_hist.distribution_bars()
    max_pct = max(pct for _, _, pct in bars) if bars else 1.0

    for label, count, pct in bars:
        bar_len = int(pct / max_pct * 30) if max_pct > 0 else 0
        bar = "#" * bar_len
        lines.append("  %-10s : %s %.1f%% (%d ops)" % (label, bar, pct, count))


def _append_errors(lines, metrics):
    if not metrics._errors:
        return

    lines.append("")
    lines.append("  Errors:")
    for msg, count in sorted(metrics._errors.items(), key=lambda x: -x[1]):
        lines.append("  %-30s %d" % (msg[:30], count))


def _merge_histograms(metrics):
    """合并所有操作的直方图为一个全局直方图。"""
    merged = LatencyHistogram()
    for op in metrics._ops.values():
        h = op.histogram
        merged._count += h.count
        merged._sum_us += h.count * h.avg_us
        if h._min_us is not None:
            if merged._min_us is None or h._min_us < merged._min_us:
                merged._min_us = h._min_us
        if h._max_us is not None:
            if merged._max_us is None or h._max_us > merged._max_us:
                merged._max_us = h._max_us
        for i, c in enumerate(h.buckets):
            merged.buckets[i] += c
        merged.overflow += h.overflow
    return merged


# ============================================================
#  辅助
# ============================================================

def _safe_pct(count, total):
    if total <= 0:
        return 0.0
    return count / float(total) * 100.0


def _ensure_dir(dirpath):
    """确保目录存在。"""
    if not os.path.isdir(dirpath):
        os.makedirs(dirpath)
