print(f"[HP60C] Expected first start_stream failed (ret={ret}), performing CAMERA_Reboot...")

    # 关键修复：CAMERA_Reboot 重置 MF 管线 (audit.py 验证此步骤可解决 -86)
    ret = _cam_sdk.camera_reboot(_camera_handle)
    if ret != 0:
        _running = False
        return {"ok": False, "msg": f"camera_reboot failed: {ret}"}
    print("[HP60C] Camera reboot completed, waiting for USB re-enumeration...")

    # 关键：销毁旧 handle
    print("[HP60C] Closing old handle...")
    _cam_sdk.close_camera(_camera_handle)
    _cam_sdk.destroy_cam_handle(_camera_handle)
    _camera_handle = ctypes.c_void_p()

    # 等待 USB 重新枚举 - 轮询 enumerate() 直到设备重新出现
    print("[HP60C] Polling for USB re-enumeration...")
    max_wait_s = 30.0
    poll_interval_s = 0.5
    deadline = time.time() + max_wait_s
    device_found = False
    while time.time() < deadline:
        r, devs = _cam_sdk.enumerate(include_net=False)
        if r == 0 and devs:
            for d in devs:
                if d.get("vid") == 13442 and d.get("pid") == 26403:
                    print(f"[HP60C] Camera re-enumerated: {d}")
                    device_found = True
                    break
        if device_found:
            break
        time.sleep(poll_interval_s)
    
    if not device_found:
        _running = False
        return {"ok": False, "msg": f"USB re-enumeration timeout after {max_wait_s}s"}

    # 重新创建 handle (使用相同的设备属性)
    print("[HP60C] Creating new handle after USB re-enumeration...")
    ret = _cam_sdk.create_cam_handle(ctypes.byref(_camera_handle), ctypes.byref(attr))
    if ret != 0:
        _running = False
        return {"ok": False, "msg": f"create_cam_handle after reboot failed: {ret}"}
    print("[HP60C] New camera handle created after reboot")