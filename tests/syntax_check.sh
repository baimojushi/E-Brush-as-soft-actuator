#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
INCLUDE="-Itests/native_stubs -Iinclude -Ilib/lsm6dsr -Ilib/tmag5273 -Ilib/mahony6"
set -x
for target in BRUSH_LINK_BLE BRUSH_LINK_WIFI; do
  g++ -std=c++17 -Wall -Wextra -Werror -fsyntax-only $INCLUDE -D"$target" src/c3_node.cpp
  g++ -std=c++17 -Wall -Wextra -Werror -fsyntax-only $INCLUDE -D"$target" src/s3_engine.cpp
  g++ -std=c++17 -Wall -Wextra -Werror -fsyntax-only $INCLUDE -D"$target" lib/tmag5273/tmag5273.cpp lib/lsm6dsr/lsm6dsr.cpp src/brush_protocol.cpp
 done
