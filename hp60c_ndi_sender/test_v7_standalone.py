#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Test v7 bridge td_init standalone"""
import sys
sys.path.insert(0, r"D:\项目\交互艺术\书法微更新\hp60c_ndi_sender")
sys.path.insert(0, r"D:\项目\交互艺术\展厅孪生\ai\hp60c_audit")

import hp60c_td_bridge_v7 as bridge

print("Testing td_init()...")
result = bridge.td_init()
print(f"td_init result: {result}")

if result.get("ok"):
    print("Waiting for frames...")
    import time
    for i in range(20):
        time.sleep(0.5)
        frame = bridge.td_get_latest_frame()
        if frame:
            print(f"Frame {i}: keys={list(frame.keys())}, depth={frame.get('depth') is not None}, rgb={frame.get('rgb') is not None}, pc={frame.get('pointcloud') is not None}")
            if frame.get('depth') is not None:
                print(f"  depth shape={frame['depth'].shape}, dtype={frame['depth'].dtype}")
            if frame.get('rgb') is not None:
                print(f"  rgb shape={frame['rgb'].shape}, dtype={frame['rgb'].dtype}")
            if frame.get('pointcloud') is not None:
                print(f"  pc shape={frame['pointcloud'].shape}, dtype={frame['pointcloud'].dtype}")
        else:
            print(f"Frame {i}: None")
    bridge.td_shutdown()
else:
    print("td_init failed")