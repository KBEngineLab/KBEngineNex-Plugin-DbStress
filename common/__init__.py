# -*- coding: utf-8 -*-
"""
DbStress — 数据库压测插件公共模块。

对外暴露唯一接口 DbStress.run()，可在任意 app（baseapp / cellapp / bots / interfaces）中调用。

用法示例:

    from plugins.DbStress.common import DbStress

    # 最简：MySQL，60s，16 并发
    DbStress.run(dbType="mysql")

    # 全参数
    DbStress.run(
        dbType="pgsql",
        dbInterface="default",
        duration=120,
        concurrency=32,
        preloadRows=50000,
        opWeights={
            "INSERT": 10,
            "POINT_SELECT": 40,
            "RANGE_SELECT": 20,
            "UPDATE": 20,
            "DELETE": 10,
        },
    )
"""

from runner import StressRunner
from config import (
    DEFAULT_DB_TYPE,
    DEFAULT_DB_INTERFACE,
    DEFAULT_DURATION,
    DEFAULT_CONCURRENCY,
    DEFAULT_PRELOAD_ROWS,
    DEFAULT_OP_WEIGHTS,
)


class _DbStressModule(object):
    """
    DbStress 对外命名空间。

    提供 run() 方法作为唯一公开入口。
    """

    @staticmethod
    def run(dbType=None, dbInterface=None, duration=None,
            concurrency=None, preloadRows=None, opWeights=None):
        """
        启动数据库压测。

        Args:
            dbType:      "mysql" | "pgsql" | "mongodb"，默认 "mysql"。
            dbInterface: kbengine.xml 中 databaseInterfaces 的名称，默认 "default"。
            duration:    施压时长（秒），默认 60。
            concurrency: 并发数（即分配的 threadID 数量），默认 16。
            preloadRows: setup 阶段预填数据行数，默认 10000。
            opWeights:   操作权重字典，默认 OLTP 80:20 读写比。

        Returns:
            None（压测异步执行，报告写入 scripts/logs/ 目录）。
        """
        db_type = (dbType or DEFAULT_DB_TYPE).lower()
        db_interface = dbInterface or DEFAULT_DB_INTERFACE
        dur = int(duration) if duration is not None else DEFAULT_DURATION
        conc = int(concurrency) if concurrency is not None else DEFAULT_CONCURRENCY
        preload = int(preloadRows) if preloadRows is not None else DEFAULT_PRELOAD_ROWS
        weights = dict(opWeights) if opWeights else dict(DEFAULT_OP_WEIGHTS)

        # 参数校验
        if db_type not in ("mysql", "pgsql", "postgresql", "postgres", "mongodb", "mongo"):
            from KBEDebug import ERROR_MSG
            ERROR_MSG("[DbStress] 不支持的数据库类型: %s，可选: mysql / pgsql / mongodb" % db_type)
            return

        if dur < 1:
            from KBEDebug import ERROR_MSG
            ERROR_MSG("[DbStress] duration 必须 >= 1，当前: %d" % dur)
            return

        if conc < 1:
            from KBEDebug import ERROR_MSG
            ERROR_MSG("[DbStress] concurrency 必须 >= 1，当前: %d" % conc)
            return

        if preload < 1:
            from KBEDebug import ERROR_MSG
            ERROR_MSG("[DbStress] preloadRows 必须 >= 1，当前: %d" % preload)
            return

        if not weights:
            from KBEDebug import ERROR_MSG
            ERROR_MSG("[DbStress] opWeights 不能为空")
            return

        # 检查权重有效性
        for k, v in weights.items():
            if v < 0:
                from KBEDebug import ERROR_MSG
                ERROR_MSG("[DbStress] opWeights 中 %s 的权重不能为负数: %d" % (k, v))
                return

        if sum(weights.values()) == 0:
            from KBEDebug import ERROR_MSG
            ERROR_MSG("[DbStress] opWeights 权重总和为 0")
            return

        runner = StressRunner(db_type, db_interface, dur, conc, preload, weights)
        runner.start()


# 对外暴露的模块级对象
DbStress = _DbStressModule()
