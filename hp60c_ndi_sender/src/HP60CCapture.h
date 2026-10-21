#pragma once

#include <atomic>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

#include "as_camera_sdk_api.h"
#include "as_camera_sdk_def.h"

struct FrameData {
    int depthWidth  = 0;
    int depthHeight = 0;
    std::vector<uint16_t> depthPixels;   // depth in mm
    std::vector<uint8_t>  depthConfidence;

    int rgbWidth  = 0;
    int rgbHeight = 0;
    std::vector<uint8_t> rgbPixels;      // BGR or RGB

    int pcWidth  = 0;
    int pcHeight = 0;
    std::vector<float> pcPoints;         // xyzxyz... (3 * pcWidth * pcHeight)

    uint64_t timestamp = 0;
    bool hasDepth = false;
    bool hasRGB   = false;
    bool hasPC    = false;
};

class HP60CCapture {
public:
    using FrameCallback = std::function<void(const FrameData&)>;

    HP60CCapture();
    ~HP60CCapture();

    bool init(const std::string& configPath);
    bool start();
    void stop();

    void setFrameCallback(FrameCallback cb) { m_callback = std::move(cb); }

    std::string getSerialNumber() const { return m_serial; }
    std::string getLastError() const { return m_lastError; }
    int getDepthWidth() const { return m_depthWidth; }
    int getDepthHeight() const { return m_depthHeight; }

private:
    static void streamCallback(AS_CAM_PTR pCamera, const AS_SDK_Data_s* pData, void* privateData);
    static void mergeCallback(AS_CAM_PTR pCamera, const AS_SDK_MERGE_s* pData, void* privateData);

    void processFrame(const AS_SDK_Data_s* pData);
    void processMerge(const AS_SDK_MERGE_s* pData);

    AS_CAM_PTR m_camera = nullptr;
    std::string m_serial;
    std::string m_lastError;
    std::atomic<bool> m_running{false};
    FrameCallback m_callback;
    int m_depthWidth = 0;
    int m_depthHeight = 0;
};