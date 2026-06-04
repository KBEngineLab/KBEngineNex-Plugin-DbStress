# -*- coding: utf-8 -*-
"""
DbStress cell 插件入口（空壳）。

cell 进程不需要压测，仅完成 import 自检以保持插件结构一致。
"""
from KBEDebug import INFO_MSG


def onInit(isReload):
    INFO_MSG("DbStress cell plugin onInit: isReload=%s" % isReload)

    from config import TABLE_NAME
    assert TABLE_NAME == "kbe_plugin_dbstress_data"


def onComponentReady(isFirstGroup):
    INFO_MSG("DbStress cell plugin onComponentReady: isFirstGroup=%s" % isFirstGroup)


def onFini():
    INFO_MSG("DbStress cell plugin onFini")
