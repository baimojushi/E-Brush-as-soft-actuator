#include "HP60CCapture.h"
#include "NDISender.h"

#include <atomic>
#include <chrono>
#include <iostream>
#include <signal.h>
#include <string>
#include <thread>

std::atomic<bool> g_running{true};

void signalHandler(int) {
    g_running = false;
}

int main(int argc, char* argv[]) {
    // Default config
    std::string configPath = "D:/Express Program Files/Angstrong Viewer V2.1.10_240109/Angstrong Viewer V2.1.10_240109/RelWithDebInfo/configurationfiles/hp60c_v2_00_20230704_configEncrypt.json";
    std::string ndiName = "HP60C";
    int targetFps = 30;
    bool sendRGB = false;
    bool sendPC = true;

    // Parse args
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--config" && i + 1 < argc) configPath = argv[++i];
        else if (arg == "--name" && i + 1 < argc) ndiName = argv[++i];
        else if (arg == "--fps" && i + 1 < argc) targetFps = std::stoi(argv[++i]);
        else if (arg == "--rgb") sendRGB = true;
        else if (arg == "--no-pc") sendPC = false;
        else if (arg == "--help") {
            std::cout << "Usage: hp60c_ndi_sender [--config <path>] [--name <NDI_name>] [--fps <30>] [--rgb] [--no-pc]\n";
            return 0;
        }
    }

    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    std::cout << "=== HP60C NDI Sender ===" << std::endl;
    std::cout << "Config: " << configPath << std::endl;
    std::cout << "NDI Name: " << ndiName << std::endl;
    std::cout << "Target FPS: " << targetFps << std::endl;
    std::cout << "Send RGB: " << (sendRGB ? "Yes" : "No") << std::endl;
    std::cout << "Send PointCloud: " << (sendPC ? "Yes" : "No") << std::endl;

    // --- Initialize HP60C Capture ---
    HP60CCapture capture;
    if (!capture.init(configPath)) {
        std::cerr << "Failed to init HP60C: " << capture.getLastError() << std::endl;
        return 1;
    }

    // Get actual depth resolution from the capture
    int depthW = capture.getDepthWidth();
    int depthH = capture.getDepthHeight();
    std::cout << "Depth resolution: " << depthW << "x" << depthH << std::endl;

    // --- Initialize NDI Sender ---
    NDISender ndi;
    if (!ndi.init(ndiName, depthW, depthH, targetFps, 1)) {
        std::cerr << "Failed to init NDI: " << ndi.getLastError() << std::endl;
        return 1;
    }

    // --- Frame callback ---
    std::atomic<int> frameCount{0};
    std::atomic<int> depthSent{0};
    std::atomic<int> rgbSent{0};
    std::atomic<int> pcSent{0};
    std::atomic<int> depthErrors{0};
    std::atomic<int> rgbErrors{0};
    std::atomic<int> pcErrors{0};
    auto lastStats = std::chrono::steady_clock::now();

    capture.setFrameCallback([&](const FrameData& frame) {
        if (!g_running) return;

        // Send depth (primary)
        if (frame.hasDepth) {
            if (ndi.sendDepth(frame.depthPixels.data(), frame.depthWidth, frame.depthHeight, frame.timestamp)) {
                depthSent++;
            } else {
                depthErrors++;
            }
        }

        // Send RGB (on same NDI stream - alternates with depth)
        if (sendRGB && frame.hasRGB) {
            if (ndi.sendRGB(frame.rgbPixels.data(), frame.rgbWidth, frame.rgbHeight, frame.timestamp, true)) {
                rgbSent++;
            } else {
                rgbErrors++;
            }
        }

        // Send point cloud as metadata
        if (sendPC && frame.hasPC) {
            if (ndi.sendPointCloud(frame.pcPoints.data(), (int)frame.pcPoints.size() / 3, frame.timestamp)) {
                pcSent++;
            } else {
                pcErrors++;
            }
        }

        // Stats
        int fc = ++frameCount;
        auto now = std::chrono::steady_clock::now();
        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - lastStats).count();
        if (elapsed >= 5) {
            std::cout << "[Stats] Frames: " << fc 
                      << " | Depth: " << depthSent.load() << " (err: " << depthErrors.load() << ")"
                      << " | RGB: " << rgbSent.load() << " (err: " << rgbErrors.load() << ")"
                      << " | PC: " << pcSent.load() << " (err: " << pcErrors.load() << ")" << std::endl;
            lastStats = now;
        }
    });

    // --- Start streaming ---
    if (!capture.start()) {
        std::cerr << "Failed to start capture: " << capture.getLastError() << std::endl;
        return 1;
    }

    std::cout << "Streaming... Press Ctrl+C to stop." << std::endl;

    // Main loop
    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    std::cout << "\nShutting down..." << std::endl;
    capture.stop();
    ndi.shutdown();

    return 0;
}