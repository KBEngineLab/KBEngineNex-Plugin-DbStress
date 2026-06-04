# -*- coding: utf-8 -*-
"""
DbStress base 插件入口。

不做自动压测，只完成基本自检确认 common 模块可 import。
对外接口通过 `from plugins.DbStress.common import DbStress` + `DbStress.run()` 调用。
"""
from KBEDebug import INFO_MSG


def onInit(isReload):
    INFO_MSG("DbStress base plugin onInit: isReload=%s" % isReload)

    # 自检：确认模块可 import
    from config import TABLE_NAME, DEFAULT_DB_TYPE
    assert TABLE_NAME == "kbe_plugin_dbstress_data"
    assert DEFAULT_DB_TYPE in ("mysql", "pgsql", "postgresql", "postgres", "mongodb", "mongo")
    INFO_MSG("DbStress base plugin self-check passed.")


def onComponentReady(isFirstGroup):
    INFO_MSG("DbStress base plugin onComponentReady: isFirstGroup=%s" % isFirstGroup)
    INFO_MSG("DbStress 已就绪，调用方式: from plugins.DbStress.common import DbStress; DbStress.run(dbType='mysql')")


def onFini():
    INFO_MSG("DbStress base plugin onFini")
