cmake_minimum_required(VERSION 3.25)
project(litert_http_service LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Persistent download/cache root to avoid repeated FetchContent downloads.
if(NOT DEFINED LITERT_SERVER_CACHE_ROOT)
  if(DEFINED ENV{LITERT_SERVER_CACHE_ROOT})
    set(LITERT_SERVER_CACHE_ROOT "$ENV{LITERT_SERVER_CACHE_ROOT}" CACHE PATH
        "Persistent cache root for LiteRT server builds")
  elseif(DEFINED ENV{HOME})
    set(LITERT_SERVER_CACHE_ROOT "$ENV{HOME}/.cache/litert-server" CACHE PATH
        "Persistent cache root for LiteRT server builds")
  endif()
endif()

if(LITERT_SERVER_CACHE_ROOT)
  execute_process(
    COMMAND ${CMAKE_COMMAND} -E make_directory "${LITERT_SERVER_CACHE_ROOT}"
    RESULT_VARIABLE _cache_mkdir_res
  )
  if(NOT _cache_mkdir_res EQUAL 0)
    message(WARNING "LITERT_SERVER_CACHE_ROOT is not writable: ${LITERT_SERVER_CACHE_ROOT}. Falling back to build-local caches.")
    unset(LITERT_SERVER_CACHE_ROOT CACHE)
    set(LITERT_SERVER_CACHE_ROOT "" CACHE PATH "Persistent cache root for LiteRT server builds" FORCE)
  endif()
endif()

if(LITERT_SERVER_CACHE_ROOT AND NOT DEFINED FETCHCONTENT_BASE_DIR)
  set(FETCHCONTENT_BASE_DIR "${LITERT_SERVER_CACHE_ROOT}/fetchcontent" CACHE PATH
      "FetchContent cache directory")
endif()
if(NOT DEFINED FETCHCONTENT_UPDATES_DISCONNECTED)
  set(FETCHCONTENT_UPDATES_DISCONNECTED ON CACHE BOOL
      "Do not update FetchContent dependencies automatically" FORCE)
endif()
if(LITERT_SERVER_CACHE_ROOT AND NOT DEFINED LITERTLM_EXTERNAL_DIR)
  set(LITERTLM_EXTERNAL_DIR "${LITERT_SERVER_CACHE_ROOT}/external" CACHE PATH
      "ExternalProject cache directory for LiteRT-LM")
endif()

set(LITERT_SERVER_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
set(LITERT_LM_SOURCE_DIR "${LITERT_SERVER_ROOT}/LiteRT-LM" CACHE PATH
    "Path to the vendored LiteRT-LM source tree")

if(NOT EXISTS "${LITERT_LM_SOURCE_DIR}/cmake/packages/litert_lm/CMakeLists.txt")
  message(FATAL_ERROR
    "LITERT_LM_SOURCE_DIR must point to a LiteRT-LM source tree. "
    "Current value: ${LITERT_LM_SOURCE_DIR}")
endif()

set(LITERTLM_PROJECT_ROOT "${LITERT_LM_SOURCE_DIR}" CACHE PATH
    "LiteRT-LM project root" FORCE)
set(LITERT_BUILD_CONFIG_DISABLE_GPU_VAL FALSE CACHE BOOL
    "Build LiteRT-LM with GPU support enabled" FORCE)
set(LITERT_BUILD_CONFIG_DISABLE_NPU_VAL TRUE CACHE BOOL
    "Disable NPU support for Jetson builds" FORCE)

add_subdirectory("${LITERT_LM_SOURCE_DIR}/cmake/packages/litert_lm"
                 "${CMAKE_BINARY_DIR}/litert_lm")

find_package(PkgConfig REQUIRED)
find_package(SQLite3 REQUIRED)
find_package(Threads REQUIRED)
pkg_check_modules(JSONCPP REQUIRED jsoncpp)

add_executable(litert_http_service
  "${LITERT_SERVER_ROOT}/src/config.cpp"
  "${LITERT_SERVER_ROOT}/src/conversation_manager.cpp"
  "${LITERT_SERVER_ROOT}/src/engine_manager.cpp"
  "${LITERT_SERVER_ROOT}/src/http_server.cpp"
  "${LITERT_SERVER_ROOT}/src/inference_backend.cpp"
  "${LITERT_SERVER_ROOT}/src/main.cpp"
  "${LITERT_SERVER_ROOT}/src/session_store.cpp"
  "${LITERT_SERVER_ROOT}/src/utils.cpp"
)

target_include_directories(litert_http_service
  PRIVATE
    "${LITERT_SERVER_ROOT}/include"
    "${LITERT_LM_SOURCE_DIR}"
    ${JSONCPP_INCLUDE_DIRS}
)

target_compile_options(litert_http_service PRIVATE
  -Wall
  -Wextra
  -Wpedantic
)

target_link_libraries(litert_http_service
  PRIVATE
    LiteRTLM::C::Engine
    SQLite3::SQLite3
    Threads::Threads
    ${JSONCPP_LIBRARIES}
)
