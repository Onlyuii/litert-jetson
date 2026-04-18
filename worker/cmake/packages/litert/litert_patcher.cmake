# Worker-specific LiteRT patch overlay.
#
# This wraps the upstream LiteRT-LM patcher, then applies a few extra fixes
# that are specific to building the lightweight resident HTTP worker:
# - restore the litert_cc_options shim as a real target
# - skip vendor dispatch libraries we do not need on Jetson
# - skip LiteRT CLI tools we do not need for the worker

include("${CMAKE_CURRENT_LIST_DIR}/litert_patcher_upstream.cmake")

set(_litert_cc_options_cmakelists "${LITERT_SRC_DIR}/cc/options/CMakeLists.txt")
set(_litert_cc_options_shim "${LITERT_PACKAGE_DIR}/shims/litert_cc_options_shim.cmake")

if(EXISTS "${_litert_cc_options_cmakelists}" AND EXISTS "${_litert_cc_options_shim}")
  file(READ "${_litert_cc_options_shim}" _litert_cc_options_content)
  file(WRITE "${_litert_cc_options_cmakelists}"
    "# Worker shim injected by agent-server to restore the real "
    "litert_cc_options target.\n\n"
    "${_litert_cc_options_content}\n")
  message(STATUS "[LiteRTLM worker] Rewrote ${_litert_cc_options_cmakelists}")
endif()

set(_litert_root_cmakelists "${LITERT_SRC_DIR}/CMakeLists.txt")
if(EXISTS "${_litert_root_cmakelists}")
  patch_file_content(
    "${_litert_root_cmakelists}"
    "add_subdirectory\\(vendors\\)"
    "# [LiteRTLM worker] vendors disabled"
    TRUE
  )
  patch_file_content(
    "${_litert_root_cmakelists}"
    "add_subdirectory\\(tools\\)"
    "# [LiteRTLM worker] tools disabled"
    TRUE
  )
endif()
