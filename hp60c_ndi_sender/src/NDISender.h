#pragma once

#include <atomic>
#include <mutex>
#include <string>
#include <vector>

#ifdef _WIN32
#include <Processing.NDI.Lib.h>
#else
#include <Processing.NDI.Lib.h>
#endif

class NDISender {
public:
    NDISender();
    ~NDISender();

    bool init(const std::string& ndiName, int width, int height, int fpsNum = 30, int fpsDen = 1);
    void shutdown();

    // Send depth frame (16-bit grayscale)
    bool sendDepth(const uint16_t* pixels, int width, int height, uint64_t timestamp);

    // Send RGB frame (BGR24 or RGB24)
    bool sendRGB(const uint8_t* pixels, int width, int height, uint64_t timestamp, bool isBGR = true);

    // Send point cloud as metadata (custom NDI metadata) - downsamples if too large
    bool sendPointCloud(const float* points, int count, uint64_t timestamp);

    // Configure depth visualization range
    void setDepthRange(float minDepthMM, float maxDepthMM);

    bool isInitialized() const { return m_initialized; }
    std::string getLastError() const { return m_lastError; }

private:
    NDIlib_send_instance_t m_sender = nullptr;
    NDIlib_video_frame_v2_t m_videoFrame = {};
    std::atomic<bool> m_initialized{false};
    std::string m_lastError;
    int m_width = 0;
    int m_height = 0;
    std::vector<uint8_t> m_depthBuffer;  // For converting 16-bit to 8-bit for NDI
    std::mutex m_mutex;                  // Thread safety for shared buffers
    float m_minDepthMM = 0.0f;
    float m_maxDepthMM = 8000.0f;
    bool m_autoDepthRange = true;
    int m_autoRangeFrames = 0;          // Frames remaining in auto-detection
    static constexpr int kAutoRangeMaxFrames = 30;  // Lock after this many frames
};