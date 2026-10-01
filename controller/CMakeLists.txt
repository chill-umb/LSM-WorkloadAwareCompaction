# The compaction-controller plugin (plan §3): librl_controller.so, built
# against the fork's public header include/rocksdb/rl_controller_host.h and
# nothing else of RocksDB, so rebuilding it never changes db_bench's hash.
#
#   cmake -S controller -B build-controller -DCMAKE_BUILD_TYPE=Release
#   cmake --build build-controller --target rl_controller
#
# -DRL_CONTROLLER_TESTS=ON adds rl_controller_tests (gtest from the fork's
# bundled copy); 01b_build_test_trees.sh builds it in Debug on the node, and
# controller/tests/run_local.sh builds the same sources with g++ directly.
cmake_minimum_required(VERSION 3.16)
project(rl_controller CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(ROCKSDB_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../lib/rocksdb"
    CACHE PATH "The fork's source tree; only its headers and gtest are read")
find_package(Threads REQUIRED)

set(RL_CONTROLLER_SOURCES
    actions.cc attribution.cc config.cc log.cc masks.cc plugin.cc policy.cc
    prior.cc rules.cc state.cc)
# This checkout's include dir first, as in run_local.sh.
set(RL_CONTROLLER_INCLUDES
    "${CMAKE_CURRENT_SOURCE_DIR}/../lib/rocksdb/include"
    "${ROCKSDB_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}")

add_library(rl_controller_objects OBJECT ${RL_CONTROLLER_SOURCES})
target_include_directories(rl_controller_objects PRIVATE ${RL_CONTROLLER_INCLUDES})
target_compile_options(rl_controller_objects PRIVATE -Wall -Wextra -Wshadow)

add_library(rl_controller SHARED $<TARGET_OBJECTS:rl_controller_objects>)
target_link_libraries(rl_controller PRIVATE Threads::Threads)
# Export only rl_controller_create and rl_controller_destroy.
set(RL_CONTROLLER_MAP "${CMAKE_CURRENT_SOURCE_DIR}/rl_controller.map")
target_link_options(rl_controller PRIVATE "-Wl,--version-script=${RL_CONTROLLER_MAP}")
set_target_properties(rl_controller PROPERTIES LINK_DEPENDS "${RL_CONTROLLER_MAP}")

option(RL_CONTROLLER_TESTS "Build the plugin's unit tests" OFF)
if(RL_CONTROLLER_TESTS)
  set(GTEST_DIR "${ROCKSDB_DIR}/third-party/gtest-1.8.1/fused-src")
  add_library(rl_controller_gtest STATIC
              "${GTEST_DIR}/gtest/gtest-all.cc" "${GTEST_DIR}/gtest/gtest_main.cc")
  target_include_directories(rl_controller_gtest SYSTEM PUBLIC "${GTEST_DIR}")
  target_link_libraries(rl_controller_gtest PUBLIC Threads::Threads)

  file(GLOB RL_CONTROLLER_TEST_SOURCES CONFIGURE_DEPENDS
       "${CMAKE_CURRENT_SOURCE_DIR}/tests/*_test.cc")
  add_executable(rl_controller_tests ${RL_CONTROLLER_TEST_SOURCES}
                                     $<TARGET_OBJECTS:rl_controller_objects>)
  target_include_directories(rl_controller_tests PRIVATE ${RL_CONTROLLER_INCLUDES})
  target_compile_options(rl_controller_tests PRIVATE -Wall -Wextra -Wshadow)
  target_compile_definitions(rl_controller_tests PRIVATE
      RL_CONTROLLER_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")
  target_link_libraries(rl_controller_tests PRIVATE rl_controller_gtest)
endif()
