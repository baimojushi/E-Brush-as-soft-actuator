#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
INCLUDE="-Itests/native_stubs -Iinclude -Ilib/lsm6dsr -Ilib/tmag5273 -Ilib/mmc5983ma -Ilib/mahony6"
set -x
for target in BRUSH_LINK_BLE BRUSH_LINK_WIFI; do
  g++ -std=c++17 -Wall -Wextra -Werror -Wno-error=unused-but-set-variable -fsyntax-only $INCLUDE -D"$target" src/c3_node.cpp
  g++ -std=c++17 -Wall -Wextra -Werror -Wno-error=unused-but-set-variable -fsyntax-only $INCLUDE -D"$target" src/s3_engine.cpp
  g++ -std=c++17 -Wall -Wextra -Werror -Wno-error=unused-but-set-variable -fsyntax-only $INCLUDE -D"$target" lib/tmag5273/tmag5273.cpp lib/mmc5983ma/mmc5983ma.cpp lib/lsm6dsr/lsm6dsr.cpp src/brush_protocol.cpp
 done

# Standalone MMC diagnostic must also compile without production sensor loop.
g++ -std=c++17 -Wall -Wextra -Werror -fsyntax-only $INCLUDE src/mmc5983_test.cpp

# C3 -> PC direct Wi-Fi adds an independent UDP clock sync endpoint.
g++ -std=c++17 -Wall -Wextra -Werror -Wno-error=unused-but-set-variable -fsyntax-only $INCLUDE -DBRUSH_LINK_WIFI -DEBRUSH_WIFI_PC_DIRECT=1 src/c3_node.cpp
