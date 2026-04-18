set(_primary_root "${PRIMARY_BINARY_DIR}")

if(NOT _primary_root)
  message(FATAL_ERROR "PRIMARY_BINARY_DIR is required")
endif()

set(_source_roots
  "${_primary_root}/litert_lm/corrosion_generated/cxxbridge/litertlm_cxx_bridge/include"
  "${_primary_root}/corrosion_generated/cxxbridge/litertlm_cxx_bridge/include"
)

set(_bridge_relpaths
  "runtime/components/rust/minijinja_template"
  "runtime/components/tool_use/rust/parsers"
)

foreach(_relpath IN LISTS _bridge_relpaths)
  set(_found_header "")
  foreach(_source_root IN LISTS _source_roots)
    if(EXISTS "${_source_root}/${_relpath}.h")
      set(_found_header "${_source_root}/${_relpath}.h")
      break()
    endif()
  endforeach()

  if(NOT _found_header)
    message(FATAL_ERROR "Could not find generated cxxbridge header for ${_relpath}")
  endif()

  set(_dest_header "${_primary_root}/corrosion_generated/cxxbridge/litertlm_cxx_bridge/include/${_relpath}.h")
  set(_dest_rs_header "${_primary_root}/corrosion_generated/cxxbridge/litertlm_cxx_bridge/include/${_relpath}.rs.h")

  get_filename_component(_dest_dir "${_dest_header}" DIRECTORY)
  file(MAKE_DIRECTORY "${_dest_dir}")
  file(COPY_FILE "${_found_header}" "${_dest_header}" ONLY_IF_DIFFERENT)
  file(COPY_FILE "${_found_header}" "${_dest_rs_header}" ONLY_IF_DIFFERENT)
endforeach()
