cmake_minimum_required(VERSION 3.25)
project(litert_http_worker LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(FETCHCONTENT_UPDATES_DISCONNECTED ON CACHE BOOL
    "Avoid network updates for already-populated FetchContent dependencies" FORCE)

find_program(RUSTC_EXECUTABLE rustc)
find_program(CARGO_EXECUTABLE cargo)
if(NOT RUSTC_EXECUTABLE OR NOT CARGO_EXECUTABLE)
  message(FATAL_ERROR
    "Building litert_http_worker requires Rust because LiteRT-LM's CMake "
    "uses Corrosion/cxxbridge.\n"
    "Install it first, for example on Ubuntu:\n"
    "  sudo apt update && sudo apt install -y rustc cargo\n")
endif()

if(NOT DEFINED LITERT_LM_SOURCE_DIR)
  set(LITERT_LM_SOURCE_DIR "/home/zeyaoz/Desktop/Projects/LiteRT-LM-v0.10.1")
endif()

if(NOT EXISTS "${LITERT_LM_SOURCE_DIR}/cmake/packages/litert_lm/CMakeLists.txt")
  message(FATAL_ERROR
    "LITERT_LM_SOURCE_DIR must point to the LiteRT-LM source tree. "
    "Current value: ${LITERT_LM_SOURCE_DIR}")
endif()

execute_process(
  COMMAND "${CARGO_EXECUTABLE}" metadata
          --manifest-path "${LITERT_LM_SOURCE_DIR}/Cargo.toml"
          --format-version 1
  RESULT_VARIABLE LITERT_HTTP_CARGO_METADATA_RESULT
  OUTPUT_QUIET
  ERROR_VARIABLE LITERT_HTTP_CARGO_METADATA_ERROR
)
if(NOT LITERT_HTTP_CARGO_METADATA_RESULT EQUAL 0)
  message(FATAL_ERROR
    "Your Cargo installation is too old for LiteRT-LM's Cargo.lock, or Cargo "
    "cannot read the LiteRT-LM Rust workspace.\n"
    "Cargo error:\n${LITERT_HTTP_CARGO_METADATA_ERROR}\n"
    "Recommended fix on this machine:\n"
    "  1. Install a newer Rust/Cargo toolchain with rustup, or upgrade cargo.\n"
    "  2. Ensure `cargo --version` and `rustc --version` are both modern and "
    "from the same toolchain.\n")
endif()

set(LITERTLM_PROJECT_ROOT "${LITERT_LM_SOURCE_DIR}" CACHE PATH
    "LiteRT-LM source root" FORCE)
set(LITERTLM_PACKAGES_OVERLAY_DIR
    "${CMAKE_CURRENT_BINARY_DIR}/litertlm_packages")
file(MAKE_DIRECTORY "${LITERTLM_PACKAGES_OVERLAY_DIR}")
file(COPY "${LITERT_LM_SOURCE_DIR}/cmake/packages/"
     DESTINATION "${LITERTLM_PACKAGES_OVERLAY_DIR}")
configure_file(
  "${LITERT_LM_SOURCE_DIR}/cmake/packages/litert/litert_patcher.cmake"
  "${LITERTLM_PACKAGES_OVERLAY_DIR}/litert/litert_patcher_upstream.cmake"
  COPYONLY
)
configure_file(
  "${CMAKE_CURRENT_SOURCE_DIR}/cmake/packages/litert/litert_patcher.cmake"
  "${LITERTLM_PACKAGES_OVERLAY_DIR}/litert/litert_patcher.cmake"
  COPYONLY
)
set(LITERTLM_PACKAGES_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}" CACHE PATH
    "LiteRT-LM packages override directory" FORCE)
set(ABSL_PACKAGE_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}/absl" CACHE PATH
    "Abseil package override directory" FORCE)
set(FLATBUFFERS_PACKAGE_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}/flatbuffers" CACHE PATH
    "FlatBuffers package override directory" FORCE)
set(LITERT_PACKAGE_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}/litert" CACHE PATH
    "LiteRT package override directory" FORCE)
set(PROTOBUF_PACKAGE_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}/protobuf" CACHE PATH
    "Protobuf package override directory" FORCE)
set(SENTENCEPIECE_PACKAGE_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}/sentencepiece" CACHE PATH
    "SentencePiece package override directory" FORCE)
set(TFLITE_PACKAGE_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}/tflite" CACHE PATH
    "TFLite package override directory" FORCE)
set(RE2_PACKAGE_DIR "${LITERTLM_PACKAGES_OVERLAY_DIR}/re2" CACHE PATH
    "RE2 package override directory" FORCE)
set(LITERTLM_SCRIPTS_OVERLAY_DIR
    "${CMAKE_CURRENT_BINARY_DIR}/litertlm_scripts")
file(MAKE_DIRECTORY "${LITERTLM_SCRIPTS_OVERLAY_DIR}")
file(COPY "${LITERT_LM_SOURCE_DIR}/cmake/scripts/"
     DESTINATION "${LITERTLM_SCRIPTS_OVERLAY_DIR}")
configure_file(
  "${CMAKE_CURRENT_SOURCE_DIR}/cmake/scripts/find_and_copy_cxxbridge.cmake"
  "${LITERTLM_SCRIPTS_OVERLAY_DIR}/find_and_copy_cxxbridge.cmake"
  COPYONLY
)
set(LITERTLM_SCRIPTS_DIR "${LITERTLM_SCRIPTS_OVERLAY_DIR}" CACHE PATH
    "LiteRT-LM scripts override directory" FORCE)

add_subdirectory("${LITERT_LM_SOURCE_DIR}/cmake/packages/litert_lm"
                 "${CMAKE_CURRENT_BINARY_DIR}/litert_lm")

set(LITERT_HTTP_WORKER_GROUP_LIBS "")
set(LITERT_HTTP_WORKER_BUILD_DEPS "")

macro(_litert_http_append_unique _list_var)
  foreach(_litert_http_item ${ARGN})
    if(NOT _litert_http_item)
      continue()
    endif()
    if(_litert_http_item MATCHES "-NOTFOUND$")
      continue()
    endif()
    list(APPEND ${_list_var} "${_litert_http_item}")
  endforeach()
  list(REMOVE_DUPLICATES ${_list_var})
endmacro()

macro(_litert_http_append_target_file _list_var _target_name)
  if(TARGET "${_target_name}")
    _litert_http_append_unique(${_list_var} "$<TARGET_FILE:${_target_name}>")
    _litert_http_append_unique(LITERT_HTTP_WORKER_BUILD_DEPS "${_target_name}")
  endif()
endmacro()

macro(_litert_http_append_target_payload _list_var _target_name _property_name)
  if(TARGET "${_target_name}")
    get_target_property(_litert_http_payload "${_target_name}" "${_property_name}")
    if(_litert_http_payload AND NOT _litert_http_payload MATCHES "-NOTFOUND$")
      set(_litert_http_payload_items ${_litert_http_payload})
      list(LENGTH _litert_http_payload_items _litert_http_payload_len)
      if(_litert_http_payload_len EQUAL 1)
        separate_arguments(_litert_http_payload_items UNIX_COMMAND "${_litert_http_payload}")
      endif()
      _litert_http_append_unique(${_list_var} ${_litert_http_payload_items})
    endif()
  endif()
endmacro()

get_property(_litert_http_local_archives GLOBAL
  PROPERTY LITERTLM_LOCAL_ARCHIVE_REGISTRY)
if(_litert_http_local_archives)
  _litert_http_append_unique(LITERT_HTTP_WORKER_GROUP_LIBS
    ${_litert_http_local_archives})
endif()

get_property(_litert_http_local_targets GLOBAL
  PROPERTY LITERTLM_LOCAL_TARGET_REGISTRY)
if(_litert_http_local_targets)
  _litert_http_append_unique(LITERT_HTTP_WORKER_BUILD_DEPS
    ${_litert_http_local_targets})
endif()

foreach(_litert_http_target IN ITEMS
    libpng_lib
    kissfft_lib
    miniaudio_lib
    minizip_lib
    antlr_lib
    zlib_lib)
  _litert_http_append_target_file(LITERT_HTTP_WORKER_GROUP_LIBS
    "${_litert_http_target}")
endforeach()

_litert_http_append_unique(LITERT_HTTP_WORKER_GROUP_LIBS
  "${CMAKE_CURRENT_BINARY_DIR}/litert_lm/liblitertlm_cxx_bridge.a"
  "${CMAKE_CURRENT_BINARY_DIR}/litert_lm/liblitert_lm_deps.a"
  "${CMAKE_CURRENT_BINARY_DIR}/litert_lm/libcxxbridge1.a"
  "${TOKENIZER_BUILD_DIR}/libtokenizers_c.a"
  "${TOKENIZER_BUILD_DIR}/libtokenizers_cpp.a")

_litert_http_append_unique(LITERT_HTTP_WORKER_BUILD_DEPS
  litertlm_cxx_bridge
  tokenizers-cpp_external)

foreach(_litert_http_target IN ITEMS
    LiteRTLM::litert::litert
    LiteRTLM::tflite::tflite
    LiteRTLM::sentencepiece::sentencepiece
    LiteRTLM::re2::re2
    LiteRTLM::flatbuffers::flatbuffers
    LiteRTLM::protobuf::libprotobuf
    LiteRTLM::absl::absl)
  _litert_http_append_target_payload(LITERT_HTTP_WORKER_GROUP_LIBS
    "${_litert_http_target}" INTERFACE_LIBRARY_PATHS)
endforeach()

if(TARGET LITERTLM_DEPS)
  target_include_directories(LITERTLM_DEPS SYSTEM INTERFACE
    "${CMAKE_CURRENT_BINARY_DIR}/litert_lm"
    "${CMAKE_CURRENT_BINARY_DIR}/litert_lm/corrosion_generated/cxxbridge/litertlm_cxx_bridge/include"
    "${CMAKE_CURRENT_BINARY_DIR}/corrosion_generated/cxxbridge/litertlm_cxx_bridge/include"
  )
endif()

if(TARGET litertlm_cxx_bridge)
  add_custom_target(litert_http_worker_bridge_headers ALL
    COMMAND "${CMAKE_COMMAND}"
      -DPRIMARY_BINARY_DIR="${CMAKE_CURRENT_BINARY_DIR}"
      -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/scripts/sync_generated_bridge_headers.cmake"
    DEPENDS litertlm_cxx_bridge
    COMMENT "Synchronizing generated cxxbridge headers for nested LiteRT-LM builds"
  )

  if(TARGET generator_complete)
    add_dependencies(generator_complete litert_http_worker_bridge_headers)
  endif()
endif()

if(TARGET generator_complete)
  add_custom_target(litert_http_worker_generated_compat ALL
    COMMAND "${CMAKE_COMMAND}"
      -DPRIMARY_BINARY_DIR="${CMAKE_CURRENT_BINARY_DIR}"
      -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/scripts/patch_generated_compat.cmake"
    DEPENDS generator_complete
    COMMENT "Applying compatibility patches to generated LiteRT-LM sources"
  )

  if(TARGET runtime_executor_llm_executor_settings_utils)
    add_dependencies(runtime_executor_llm_executor_settings_utils
      litert_http_worker_generated_compat)
  endif()
endif()

find_package(Threads REQUIRED)

add_executable(litert_http_worker
  litert_http_worker.cpp
)

target_include_directories(litert_http_worker
  PRIVATE
    "${LITERT_LM_SOURCE_DIR}"
)

target_compile_options(litert_http_worker PRIVATE
  -Wall
  -Wextra
  -Wpedantic
)

target_link_libraries(litert_http_worker
  PRIVATE
    -Wl,--start-group
    ${LITERT_HTTP_WORKER_GROUP_LIBS}
    -Wl,--end-group
    LiteRTLM::nlohmann_json::nlohmann_json
    ${CMAKE_DL_LIBS}
    m
    rt
    util
    Threads::Threads
)

if(TARGET LITERTLM_DEPS)
  add_dependencies(litert_http_worker LITERTLM_DEPS)
endif()

if(LITERT_HTTP_WORKER_BUILD_DEPS)
  add_dependencies(litert_http_worker ${LITERT_HTTP_WORKER_BUILD_DEPS})
endif()
