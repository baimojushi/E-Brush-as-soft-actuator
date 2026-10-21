#include "HP60CCapture.h"

#include <iostream>
#include <cstring>
#include <chrono>

HP60CCapture::HP60CCapture() = default;

HP60CCapture::~HP60CCapture() {
    stop();
    if (m_camera) {
        AS_SDK_CloseCamera(m_camera);
        m_camera = nullptr;
    }
    AS_SDK_Deinit();
}

bool HP60CCapture::init(const std::string& configPath) {
    int ret = AS_SDK_Init();
    if (ret != 0) {
        m_lastError = "AS_SDK_Init failed, code=" + std::to_string(ret);
        return false;
    }

    // Enumerate cameras
    std::list<AS_CAM_PTR> devList;
    ret = AS_SDK_GetCameraList(devList);
    if (ret != 0 || devList.empty()) {
        m_lastError = "No Angstrong cameras found (ret=" + std::to_string(ret) + ")";
        return false;
    }

    // Pick first camera
    m_camera = devList.front();
    devList.pop_front();
    AS_SDK_FreeCameraList(devList);

    // Get serial number
    char sn[128] = {0};
    ret = AS_SDK_GetSerialNumber(m_camera, sn, sizeof(sn));
    if (ret == 0) {
        m_serial = sn;
        std::cout << "[HP60C] Camera SN: " << m_serial << std::endl;
    }

    // Get camera model
    AS_SDK_CAM_MODEL_E model = AS_SDK_CAM_MODEL_UNKNOWN;
    ret = AS_SDK_GetCameraModel(m_camera, model);
    if (ret == 0) {
        std::cout << "[HP60C] Camera model: " << static_cast<int>(model) << std::endl;
        if (model != AS_SDK_CAM_MODEL_HP60C && model != AS_SDK_CAM_MODEL_HP60CN) {
            std::cout << "[HP60C] Warning: expected HP60C/HP60CN, got model " << static_cast<int>(model) << std::endl;
        }
    }

    // Open camera with config
    ret = AS_SDK_OpenCamera(m_camera, configPath.c_str());
    if (ret != 0) {
        m_lastError = "AS_SDK_OpenCamera failed, code=" + std::to_string(ret);
        return false;
    }
    std::cout << "[HP60C] Camera opened successfully." << std::endl;

    // Get stream parameters for info and to set depth resolution
    AS_STREAM_Param_s param;
    ret = AS_SDK_GetStreamParam(m_camera, &param);
    if (ret == 0) {
        m_depthWidth = param.width;
        m_depthHeight = param.height;
        std::cout << "[HP60C] Stream: " << param.width << "x" << param.height << " @ " << param.fps << "fps" << std::endl;
    } else {
        m_lastError = "AS_SDK_GetStreamParam failed, code=" + std::to_string(ret);
        return false;
    }

    // Register stream callback
    AS_CAM_Stream_Cb_s streamCb;
    streamCb.callback = &HP60CCapture::streamCallback;
    streamCb.privateData = this;
    ret = AS_SDK_RegisterStreamCallback(m_camera, &streamCb);
    if (ret != 0) {
        m_lastError = "AS_SDK_RegisterStreamCallback failed, code=" + std::to_string(ret);
        return false;
    }

    // Register merge callback (for merged depth+pointcloud)
    AS_CAM_Merge_Cb_s mergeCb;
    mergeCb.callback = &HP60CCapture::mergeCallback;
    mergeCb.privateData = this;
    ret = AS_SDK_RegisterMergeFrameCallback(m_camera, &mergeCb);
    if (ret != 0) {
        std::cout << "[HP60C] Warning: RegisterMergeFrameCallback failed (non-critical)" << std::endl;
    }

    return true;
}

bool HP60CCapture::start() {
    if (!m_camera) {
        m_lastError = "Camera not initialized";
        return false;
    }
    int ret = AS_SDK_StartStream(m_camera, 0);
    if (ret != 0) {
        m_lastError = "AS_SDK_StartStream failed, code=" + std::to_string(ret);
        return false;
    }
    m_running = true;
    std::cout << "[HP60C] Stream started." << std::endl;
    return true;
}

void HP60CCapture::stop() {
    if (m_running && m_camera) {
        AS_SDK_StopStream(m_camera, 0);
        std::cout << "[HP60C] Stream stopped." << std::endl;
    }
    m_running = false;
}

void HP60CCapture::streamCallback(AS_CAM_PTR pCamera, const AS_SDK_Data_s* pData, void* privateData) {
    auto* self = static_cast<HP60CCapture*>(privateData);
    if (self && pData) {
        self->processFrame(pData);
    }
}

void HP60CCapture::mergeCallback(AS_CAM_PTR pCamera, const AS_SDK_MERGE_s* pData, void* privateData) {
    auto* self = static_cast<HP60CCapture*>(privateData);
    if (self && pData) {
        self->processMerge(pData);
    }
}

void HP60CCapture::processFrame(const AS_SDK_Data_s* pData) {
    if (!m_callback) return;

    FrameData frame;
    frame.timestamp = pData->depthImg.ts;

    // --- Depth ---
    if (pData->depthImg.data && pData->depthImg.size > 0) {
        frame.depthWidth  = pData->depthImg.width;
        frame.depthHeight = pData->depthImg.height;
        size_t n = (size_t)frame.depthWidth * frame.depthHeight;
        frame.depthPixels.resize(n);
        std::memcpy(frame.depthPixels.data(), pData->depthImg.data,
                    std::min((size_t)pData->depthImg.size, n * sizeof(uint16_t)));
        frame.hasDepth = true;
    }

    // --- RGB ---
    if (pData->rgbImg.data && pData->rgbImg.size > 0) {
        frame.rgbWidth  = pData->rgbImg.width;
        frame.rgbHeight = pData->rgbImg.height;
        size_t n = pData->rgbImg.size;
        frame.rgbPixels.resize(n);
        std::memcpy(frame.rgbPixels.data(), pData->rgbImg.data, n);
        frame.hasRGB = true;
    }

    // --- Point Cloud (from depth frame's pointCloud field) ---
    if (pData->pointCloud.data && pData->pointCloud.size > 0) {
        frame.pcWidth  = pData->pointCloud.width;
        frame.pcHeight = pData->pointCloud.height;
        size_t n = pData->pointCloud.size / sizeof(float);
        frame.pcPoints.resize(n);
        std::memcpy(frame.pcPoints.data(), pData->pointCloud.data, n * sizeof(float));
        frame.hasPC = true;
    }

    m_callback(frame);
}

void HP60CCapture::processMerge(const AS_SDK_MERGE_s* pData) {
    // Merge frames contain aligned depth + pointcloud
    // We can use this as a fallback if the stream callback doesn't provide pointcloud
    if (!m_callback) return;
    // The merge callback supplements the stream callback; actual frame delivery
    // happens in processFrame. Merge data can be used for higher-quality pointclouds.
    // For now, we log it.
    // std::cout << "[HP60C] Merge frame: depth " << pData->depthImg.width << "x" << pData->depthImg.height << std::endl;
}