cmake_minimum_required(VERSION 3.10)
project(LSM-WorkloadAwareCompaction VERSION 1.0)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3")
set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" )

if(JEMALLOC)
	add_library(jemalloc INTERFACE)
	target_compile_definitions(jemalloc INTERFACE ROCKSDB_JEMALLOC JEMALLOC_NO_DEMANGLE)
	target_link_libraries(jemalloc INTERFACE ${JEMALLOC_LIB} pthread rt)
else()
	add_library(jemalloc INTERFACE)
	target_link_libraries(jemalloc INTERFACE pthread rt)
endif()

set(WITH_TESTS OFF CACHE BOOL "Build with tests" FORCE)
set(WITH_TOOLS OFF CACHE BOOL "Build with tools" FORCE)
set(WITH_ALL_TESTS OFF CACHE BOOL "Build with all tests" FORCE)
set(WITH_BENCHMARK_TOOLS OFF CACHE BOOL "Build with benchmark tools" FORCE)
set(WITH_CORE_TOOLS OFF CACHE BOOL "Build with core tools" FORCE)
set(WITH_TRACE_TOOLS OFF CACHE BOOL "Build with trace tools" FORCE)
set(FAIL_ON_WARNINGS OFF CACHE BOOL "Fail on warnings" FORCE)
set(USE_RTTI ON CACHE STRING "Enable RTTI in RocksDB builds" FORCE)

file(GLOB_RECURSE SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cc")

add_subdirectory(lib/rocksdb)

add_executable(db_runner ${SOURCES})

target_include_directories(db_runner PRIVATE ${CMAKE_SOURCE_DIR}/include)

target_link_libraries(db_runner
	PRIVATE
		rocksdb
		jemalloc
)

target_compile_definitions(db_runner PRIVATE PROFILE)

add_custom_target(tectonic-cli ALL
	COMMAND RUSTUP_TOOLCHAIN=nightly cargo build --release
	COMMAND ${CMAKE_COMMAND} -E copy
		${CMAKE_SOURCE_DIR}/lib/tectonic/target/release/tectonic-cli
		${CMAKE_SOURCE_DIR}/bin/tectonic-cli
	WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}/lib/tectonic
	COMMENT "Building tectonic-cli in release mode"
)

add_dependencies(db_runner tectonic-cli)
