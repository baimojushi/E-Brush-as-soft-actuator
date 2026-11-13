#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
sh tests/syntax_check.sh
BIN="$(mktemp /tmp/ebrush_link_test.XXXXXX)"
trap 'rm -f "$BIN"' EXIT HUP INT TERM
c++ -std=c++17 -Wall -Wextra -Werror -Iinclude \
    tests/test_link.cpp src/ebrush_link.cpp src/s3_adaptive.cpp -o "$BIN"
"$BIN"
c++ -std=c++17 -Wall -Wextra -Werror -Itests/mock_wire \
    -Itests/native_stubs -Ilib/tmag5273 \
    tests/test_hall_fresh.cpp lib/tmag5273/tmag5273.cpp -o "$BIN"
"$BIN"
c++ -std=c++17 -Wall -Wextra -Werror -Iinclude -Itests/mock_mmc_wire \
    -Itests/native_stubs -Ilib/mmc5983ma \
    tests/test_mmc5983ma.cpp lib/mmc5983ma/mmc5983ma.cpp -o "$BIN"
"$BIN"
python3 tests/test_python.py
python3 tests/test_c3_direct.py
python3 studio/tests/test_brush_port_detection.py
