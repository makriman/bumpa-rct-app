#!/bin/sh
set -eu

source_archive="$1"
arch="$(dpkg --print-architecture)"
triplet="$(gcc -dumpmachine)"
libc_version="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/source" "$work/package/DEBIAN" /out
tar -xzf "$source_archive" --strip-components=1 -C "$work/source"
cmake -S "$work/source" -B "$work/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
  -DBROTLI_BUILD_TOOLS=OFF -DCMAKE_INSTALL_PREFIX=/usr \
  -DCMAKE_INSTALL_LIBDIR="lib/$triplet"
cmake --build "$work/build" --parallel 2
DESTDIR="$work/package" cmake --install "$work/build"
rm -rf "$work/package/usr/include" \
  "$work/package/usr/lib/$triplet/pkgconfig" \
  "$work/package/usr/lib/$triplet/cmake"
printf 'Package: libbrotli1\nSource: brotli\nVersion: 1.2.1-0bumpa1\nArchitecture: %s\nMaintainer: Bumpa Bestie <security@example.invalid>\nDepends: libc6 (>= %s)\nSection: libs\nPriority: optional\nDescription: Brotli 1.2.1 security overlay for CVE-2026-111497\n' \
  "$arch" "$libc_version" > "$work/package/DEBIAN/control"
dpkg-deb --root-owner-group --build "$work/package" \
  "/out/libbrotli1_1.2.1-0bumpa1_${arch}.deb"
