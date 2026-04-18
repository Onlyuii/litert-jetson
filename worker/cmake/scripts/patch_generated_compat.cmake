set(_primary_root "${PRIMARY_BINARY_DIR}")

if(NOT _primary_root)
  message(FATAL_ERROR "PRIMARY_BINARY_DIR is required")
endif()

set(_target_file
  "${_primary_root}/generated/src/runtime/executor/llm_executor_settings_utils.cc")

if(NOT EXISTS "${_target_file}")
  message(FATAL_ERROR
    "Expected generated source was not found: ${_target_file}")
endif()

file(READ "${_target_file}" _contents)

set(_old_block
"      runtime_options.SetDisableDelegateClustering(
          advanced_settings.disable_delegate_clustering);
")

set(_new_block
"      // Older LiteRT releases do not expose this RuntimeOptions setter.
      (void)advanced_settings;
")

string(FIND "${_contents}" "${_old_block}" _needle_pos)
if(_needle_pos EQUAL -1)
  if(_contents MATCHES "SetDisableDelegateClustering")
    message(FATAL_ERROR
      "Compatibility patch could not rewrite SetDisableDelegateClustering in "
      "${_target_file}")
  endif()
  message(STATUS
    "Compatibility patch already applied to ${_target_file}")
  return()
endif()

string(REPLACE "${_old_block}" "${_new_block}" _patched_contents "${_contents}")
file(WRITE "${_target_file}" "${_patched_contents}")
message(STATUS
  "Patched generated compatibility gap in ${_target_file}")
