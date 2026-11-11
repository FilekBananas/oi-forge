// Shim <bits/stdc++.h> dla kompilatorów bez libstdc++ (clang/libc++ na macOS, MSVC).
// Dołącza całą bibliotekę standardową — tak jak oryginał z GCC.
#pragma once
#ifndef OIFORGE_BITS_STDCXX_H
#define OIFORGE_BITS_STDCXX_H

#include <cassert>
#include <cctype>
#include <cerrno>
#include <cfloat>
#include <climits>
#include <clocale>
#include <cmath>
#include <csetjmp>
#include <csignal>
#include <cstdarg>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <cstdint>
#include <cinttypes>
#include <cwchar>
#include <cwctype>

#include <algorithm>
#include <array>
#include <atomic>
#include <bitset>
#include <chrono>
#include <complex>
#include <condition_variable>
#include <deque>
#include <exception>
#include <forward_list>
#include <fstream>
#include <functional>
#include <initializer_list>
#include <iomanip>
#include <ios>
#include <iosfwd>
#include <iostream>
#include <istream>
#include <iterator>
#include <limits>
#include <list>
#include <locale>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <numeric>
#include <ostream>
#include <queue>
#include <random>
#include <ratio>
#include <regex>
#include <set>
#include <sstream>
#include <stack>
#include <stdexcept>
#include <streambuf>
#include <string>
#include <system_error>
#include <thread>
#include <tuple>
#include <type_traits>
#include <typeindex>
#include <typeinfo>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <valarray>
#include <vector>

#if __cplusplus >= 201703L
#include <any>
#include <charconv>
#include <filesystem>
#include <optional>
#include <string_view>
#include <variant>
#include <execution>
#endif

#if __cplusplus >= 202002L
#include <barrier>
#include <bit>
#include <compare>
#include <concepts>
#include <latch>
#include <numbers>
#include <ranges>
#include <span>
#include <semaphore>
#include <version>
#endif

// Nowsze nagłówki, jeśli dostępne (GCC dołącza je w swoim bits/stdc++.h)
#if defined(__has_include)
#  if __cplusplus >= 201703L
#    if __has_include(<memory_resource>)
#      include <memory_resource>
#    endif
#    if __has_include(<shared_mutex>)
#      include <shared_mutex>
#    endif
#    if __has_include(<scoped_allocator>)
#      include <scoped_allocator>
#    endif
#    if __has_include(<cuchar>)
#      include <cuchar>
#    endif
#  endif
#  if __cplusplus >= 202002L
#    if __has_include(<format>)
#      include <format>
#    endif
#    if __has_include(<source_location>)
#      include <source_location>
#    endif
#    if __has_include(<stop_token>)
#      include <stop_token>
#    endif
#    if __has_include(<coroutine>)
#      include <coroutine>
#    endif
#  endif
#  if __cplusplus > 202002L
#    if __has_include(<expected>)
#      include <expected>
#    endif
#    if __has_include(<print>)
#      include <print>
#    endif
#    if __has_include(<stdfloat>)
#      include <stdfloat>
#    endif
#  endif
#endif

#endif
