# TouchDesigner Script CHOP callbacks
#
# 推荐把 BrushTD 实例存到当前 Base COMP：
#     me.parent().store('brush_td_bridge', bridge)
#
# 此脚本放到同一 Base COMP 内的 Script CHOP。

BRIDGE_KEY = "brush_td_bridge"


def onSetupParameters(scriptOp):
    return


def onPulse(par):
    return


def onCook(scriptOp):
    scriptOp.clear()
    owner = scriptOp.parent()
    bridge = owner.fetch(BRIDGE_KEY, None)
    if bridge is None:
        return

    bridge.poll()
    channels = bridge.channels(aligned=True)
    for name, value in channels.items():
        ch = scriptOp.appendChan(name)
        ch[0] = value
