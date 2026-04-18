# Compatible with Cargo target layouts that place libcxxbridge1.a in
# deeply nested build directories, e.g.:
#   <build>/cargo/<workspace>/<triple>/release/build/cxx-*/out/libcxxbridge1.a
#
# LiteRT-LM sometimes invokes this helper from a nested CMake binary dir like:
#   <top-build>/litert_lm
# while Corrosion writes Cargo artifacts under:
#   <top-build>/cargo
# so we search a few parent build roots instead of assuming a single layout.

set(_search_roots "${CMAKE_BINARY_DIR}")

get_filename_component(_parent_build_dir "${CMAKE_BINARY_DIR}" DIRECTORY)
if(_parent_build_dir AND NOT _parent_build_dir STREQUAL CMAKE_BINARY_DIR)
  list(APPEND _search_roots "${_parent_build_dir}")
endif()

get_filename_component(_grandparent_build_dir "${_parent_build_dir}" DIRECTORY)
if(_grandparent_build_dir
   AND NOT _grandparent_build_dir STREQUAL _parent_build_dir
   AND NOT _grandparent_build_dir STREQUAL CMAKE_BINARY_DIR)
  list(APPEND _search_roots "${_grandparent_build_dir}")
endif()

list(REMOVE_DUPLICATES _search_roots)

set(_candidate_paths "")
foreach(_root IN LISTS _search_roots)
  if(EXISTS "${_root}/cargo")
    file(GLOB_RECURSE _root_static_libs
        LIST_DIRECTORIES false
        "${_root}/cargo/*.a"
    )
    list(APPEND _candidate_paths ${_root_static_libs})
  endif()
endforeach()

set(BRIDGE_LIB "")
foreach(candidate IN LISTS _candidate_paths)
  get_filename_component(candidate_name "${candidate}" NAME)
  if(candidate_name STREQUAL "libcxxbridge1.a")
    list(APPEND BRIDGE_LIB "${candidate}")
  endif()
endforeach()

if(BRIDGE_LIB)
    list(GET BRIDGE_LIB 0 ACTUAL_PATH)
    message(STATUS "Found cxxbridge at: ${ACTUAL_PATH}")
    file(COPY "${ACTUAL_PATH}" DESTINATION "${CMAKE_BINARY_DIR}")
else()
    string(REPLACE ";" "\n  - " _search_roots_message "${_search_roots}")
    message(FATAL_ERROR
      "Could not find libcxxbridge1.a.\n"
      "Searched Cargo artifacts under:\n"
      "  - ${_search_roots_message}")
endif()
