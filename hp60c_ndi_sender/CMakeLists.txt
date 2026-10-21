cmake_minimum_required(VERSION 3.16)
project(hp60c_ndi_sender LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# --- Paths (adjust if needed) ---
set(ANGSTRONG_SDK_DIR "D:/Express Program Files/Angstrong Viewer V2.1.10_240109/Angstrong Viewer V2.1.10_240109/RelWithDebInfo")
set(NDI_SDK_DIR "C:/Program Files/NDI/NDI 5 SDK")  # Default NDI 5 SDK install path

# --- Find Angstrong SDK ---
find_library(ANGSTRONG_CAMERA_SDK
    NAMES AngstrongCameraSdk
    PATHS ${ANGSTRONG_SDK_DIR}
    NO_DEFAULT_PATH
)
find_path(ANGSTRONG_INCLUDE_DIR
    NAMES as_camera_sdk_api.h
    PATHS ${ANGSTRONG_SDK_DIR}
    NO_DEFAULT_PATH
)

# --- Find NDI SDK ---
find_library(NDI_LIB
    NAMES Processing.NDI.Lib.x64
    PATHS ${NDI_SDK_DIR}/lib/x64
    NO_DEFAULT_PATH
)
find_path(NDI_INCLUDE_DIR
    NAMES Processing.NDI.Lib.h
    PATHS ${NDI_SDK_DIR}/include
    NO_DEFAULT_PATH
)

if(NOT ANGSTRONG_CAMERA_SDK)
    message(FATAL_ERROR "AngstrongCameraSdk.lib not found in ${ANGSTRONG_SDK_DIR}")
endif()
if(NOT ANGSTRONG_INCLUDE_DIR)
    message(FATAL_ERROR "Angstrong SDK headers not found in ${ANGSTRONG_SDK_DIR}")
endif()
if(NOT NDI_LIB)
    message(WARNING "NDI SDK not found at ${NDI_SDK_DIR}. Install NDI 5 SDK from https://ndi.video/")
    message(WARNING "Build will succeed but NDI sending will be disabled.")
endif()

add_executable(hp60c_ndi_sender
    src/main.cpp
    src/HP60CCapture.cpp
    src/HP60CCapture.h
    src/NDISender.cpp
    src/NDISender.h
)

target_include_directories(hp60c_ndi_sender PRIVATE
    ${ANGSTRONG_INCLUDE_DIR}
    ${NDI_INCLUDE_DIR}
    src
)

target_link_directories(hp60c_ndi_sender PRIVATE
    ${ANGSTRONG_SDK_DIR}
)

target_link_libraries(hp60c_ndi_sender PRIVATE
    ${ANGSTRONG_CAMERA_SDK}
    ${NDI_LIB}
    # Windows system libs
    ws2_32
    iphlpapi
    user32
    gdi32
)

# Copy DLLs to output directory for easy run
add_custom_command(TARGET hp60c_ndi_sender POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${ANGSTRONG_SDK_DIR}/AngstrongCameraSdk.dll"
        $<TARGET_FILE_DIR:hp60c_ndi_sender>
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${NDI_SDK_DIR}/bin/x64/Processing.NDI.Lib.x64.dll"
        $<TARGET_FILE_DIR:hp60c_ndi_sender>
    COMMENT "Copying runtime DLLs to output directory"
)