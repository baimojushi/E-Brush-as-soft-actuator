#include "NDISender.h"

#include <iostream>
#include <cstring>
#include <algorithm>
#include <sstream>
#include <iomanip>

NDISender::NDISender() = default;

NDISender::~NDISender() {
    shutdown();
}

bool NDISender::init(const std::string& ndiName, int width, int height, int fpsNum, int fpsDen) {
    if (!NDIlib_initialize()) {
        m_lastError = "NDIlib_initialize failed";
        return false;
    }

    NDIlib_send_create_t createDesc = {};
    createDesc.p_ndi_name = ndiName.c_str();
    createDesc.p_groups = nullptr;
    createDesc.clock_video = true;
    createDesc.clock_audio = false;

    m_sender = NDIlib_send_create(&createDesc);
    if (!m_sender) {
        m_lastError = "NDIlib_send_create failed for name: " + ndiName;
        NDIlib_destroy();
        return false;
    }

    m_width = width;
    m_height = height;

    // Setup video frame template
    m_videoFrame = {};
    m_videoFrame.xres = width;
    m_videoFrame.yres = height;
    m_videoFrame.FourCC = NDIlib_FourCC_type_BGRX;  // 32-bit BGRX
    m_videoFrame.frame_rate_N = fpsNum;
    m_videoFrame.frame_rate_D = fpsDen;
    m_videoFrame.picture_aspect_ratio = (float)width / (float)height;
    m_videoFrame.frame_format_type = NDIlib_frame_format_type_progressive;
    m_videoFrame.timecode = NDIlib_send_timecode_synthesize;

    // Pre-allocate depth conversion buffer (16-bit -> 8-bit grayscale -> BGRX)
    m_depthBuffer.resize((size_t)width * height * 4);

    m_initialized = true;
    m_autoRangeFrames = 0;  // Reset auto-detection counter on init
    std::cout << "[NDI] Sender created: " << ndiName << " (" << width << "x" << height << " @ " << fpsNum << "/" << fpsDen << ")" << std::endl;
    return true;
}

void NDISender::shutdown() {
    if (m_sender) {
        NDIlib_send_destroy(m_sender);
        m_sender = nullptr;
    }
    NDIlib_destroy();
    m_initialized = false;
}

void NDISender::setDepthRange(float minDepthMM, float maxDepthMM) {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_minDepthMM = minDepthMM;
    m_maxDepthMM = maxDepthMM;
    m_autoDepthRange = false;
    m_autoRangeFrames = kAutoRangeMaxFrames;  // Mark as complete to prevent re-enabling
    std::cout << "[NDI] Depth range set: " << minDepthMM << " - " << maxDepthMM << " mm" << std::endl;
}

bool NDISender::sendDepth(const uint16_t* pixels, int width, int height, uint64_t timestamp) {
    if (!m_initialized || !pixels) return false;

    std::lock_guard<std::mutex> lock(m_mutex);

    // Resize buffer if resolution changed
    size_t n = (size_t)width * height;
    if (m_depthBuffer.size() < n * 4) {
        m_depthBuffer.resize(n * 4);
    }

    // Auto-detect depth range on first kAutoRangeMaxFrames frames, then lock
    float minDepth = m_minDepthMM;
    float maxDepth = m_maxDepthMM;
    if (m_autoDepthRange) {
        if (m_autoRangeFrames < kAutoRangeMaxFrames) {
            // Still in auto-detection phase: scan frame for min/max
            float frameMin = 65535.0f;
            float frameMax = 0.0f;
            for (size_t i = 0; i < n; ++i) {
                float d = (float)pixels[i];
                if (d > 0.0f && d < frameMin) frameMin = d;
                if (d > frameMax) frameMax = d;
            }
            // Update running min/max across frames
            if (frameMax > frameMin) {
                if (m_autoRangeFrames == 0) {
                    minDepth = frameMin;
                    maxDepth = frameMax;
                } else {
                    minDepth = std::min(m_minDepthMM, frameMin);
                    maxDepth = std::max(m_maxDepthMM, frameMax);
                }
                // Add small margin
                float range = maxDepth - minDepth;
                minDepth = std::max(0.0f, minDepth - range * 0.05f);
                maxDepth = maxDepth + range * 0.05f;
                // Store for next frame
                m_minDepthMM = minDepth;
                m_maxDepthMM = maxDepth;
            }
            m_autoRangeFrames++;
        } else {
            // Auto-detection complete: lock the range
            m_autoDepthRange = false;
            minDepth = m_minDepthMM;
            maxDepth = m_maxDepthMM;
            std::cout << "[NDI] Auto depth range locked: " << minDepth << " - " << maxDepth << " mm" << std::endl;
        }
    }

    uint8_t* dst = m_depthBuffer.data();
    float range = maxDepth - minDepth;
    if (range <= 0.0f) range = 1.0f;

    for (size_t i = 0; i < n; ++i) {
        float d = (float)pixels[i];
        if (d <= 0.0f) {
            // Invalid/zero depth -> black
            dst[i * 4 + 0] = 0;
            dst[i * 4 + 1] = 0;
            dst[i * 4 + 2] = 0;
            dst[i * 4 + 3] = 255;
        } else {
            float normalized = (d - minDepth) / range;
            normalized = std::clamp(normalized, 0.0f, 1.0f);
            uint8_t v = (uint8_t)(normalized * 255.0f);
            // BGRX format (grayscale)
            dst[i * 4 + 0] = v;  // B
            dst[i * 4 + 1] = v;  // G
            dst[i * 4 + 2] = v;  // R
            dst[i * 4 + 3] = 255; // X
        }
    }

    m_videoFrame.p_data = dst;
    m_videoFrame.line_stride_in_bytes = width * 4;
    m_videoFrame.timecode = NDIlib_send_timecode_synthesize;

    NDIlib_send_send_video_v2(m_sender, &m_videoFrame);
    return true;
}

bool NDISender::sendRGB(const uint8_t* pixels, int width, int height, uint64_t timestamp, bool isBGR) {
    if (!m_initialized || !pixels) return false;

    std::lock_guard<std::mutex> lock(m_mutex);

    // NDI expects BGRX (32-bit). Convert from BGR24/RGB24.
    size_t n = (size_t)width * height;
    if (m_depthBuffer.size() < n * 4) {
        m_depthBuffer.resize(n * 4);
    }
    uint8_t* dst = m_depthBuffer.data();

    if (isBGR) {
        // BGR24 -> BGRX
        for (size_t i = 0; i < n; ++i) {
            dst[i * 4 + 0] = pixels[i * 3 + 0];
            dst[i * 4 + 1] = pixels[i * 3 + 1];
            dst[i * 4 + 2] = pixels[i * 3 + 2];
            dst[i * 4 + 3] = 255;
        }
    } else {
        // RGB24 -> BGRX (swap R/B)
        for (size_t i = 0; i < n; ++i) {
            dst[i * 4 + 0] = pixels[i * 3 + 2];
            dst[i * 4 + 1] = pixels[i * 3 + 1];
            dst[i * 4 + 2] = pixels[i * 3 + 0];
            dst[i * 4 + 3] = 255;
        }
    }

    m_videoFrame.p_data = dst;
    m_videoFrame.line_stride_in_bytes = width * 4;
    m_videoFrame.timecode = NDIlib_send_timecode_synthesize;

    NDIlib_send_send_video_v2(m_sender, &m_videoFrame);
    return true;
}

bool NDISender::sendPointCloud(const float* points, int count, uint64_t timestamp) {
    if (!m_initialized || !points || count <= 0) return false;

    // NDI metadata has size limits (~64KB typical). Downsample large point clouds.
    const int MAX_POINTS = 2000;  // ~60KB XML
    int sendCount = count;
    int stride = 1;
    if (count > MAX_POINTS) {
        stride = (count + MAX_POINTS - 1) / MAX_POINTS;
        sendCount = (count + stride - 1) / stride;
    }

    // Build compact XML metadata
    std::ostringstream xml;
    xml << "<pointcloud count=\"" << sendCount << "\" total=\"" << count 
        << "\" stride=\"" << stride << "\" timestamp=\"" << timestamp << "\">";
    
    for (int i = 0, idx = 0; i < count && idx < sendCount; i += stride, ++idx) {
        xml << "<p x=\"" << std::fixed << std::setprecision(3) << points[i * 3 + 0] 
            << "\" y=\"" << points[i * 3 + 1] 
            << "\" z=\"" << points[i * 3 + 2] << "\"/>";
    }
    xml << "</pointcloud>";

    std::string xmlStr = xml.str();
    
    NDIlib_metadata_frame_t meta = {};
    meta.p_data = xmlStr.c_str();
    meta.length = (int)xmlStr.size();
    meta.timecode = NDIlib_send_timecode_synthesize;

    NDIlib_send_send_metadata(m_sender, &meta);
    return true;
}