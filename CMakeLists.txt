cmake_minimum_required(VERSION 3.10)
project(LSM-WorkloadAwareCompaction VERSION 1.0)

# Configures lib/rocksdb only, so that this tree's compile_commands.json can
# drive the local -fsyntax-only check (CLAUDE.md). The node builds with
# scripts/dbbench_pipeline/01_build_rocksdb.sh instead.

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3")
set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" )

set(WITH_TESTS OFF CACHE BOOL "Build with tests" FORCE)
set(WITH_TOOLS OFF CACHE BOOL "Build with tools" FORCE)
set(WITH_ALL_TESTS OFF CACHE BOOL "Build with all tests" FORCE)
set(WITH_BENCHMARK_TOOLS OFF CACHE BOOL "Build with benchmark tools" FORCE)
set(WITH_CORE_TOOLS OFF CACHE BOOL "Build with core tools" FORCE)
set(WITH_TRACE_TOOLS OFF CACHE BOOL "Build with trace tools" FORCE)
set(FAIL_ON_WARNINGS OFF CACHE BOOL "Fail on warnings" FORCE)
set(USE_RTTI ON CACHE STRING "Enable RTTI in RocksDB builds" FORCE)

add_subdirectory(lib/rocksdb)
