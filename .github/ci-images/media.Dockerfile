# SPDX-License-Identifier: Apache-2.0
#
# Ready-made media test prerequisites (ffmpeg + TSDuck) for the media suites
# in .github/workflows/ci-test.yml ("Unit tests") and
# .github/workflows/deterministic-detectors.yml ("randomized-suite").
#
# Why this is not used as a `container:` job: both of those jobs run
# testcontainers against a real Postgres and probe /var/run/docker.sock
# (TEST-001). A GitHub container job does not get the runner's Docker daemon,
# so containerizing them would silently break the Postgres proof. Instead the
# image carries /opt/ci-media/rootfs.tar -- exactly the files the newly
# installed packages own -- and scripts/ci/install_media_test_prerequisites.sh
# extracts it onto the ubuntu-24.04 runner (same distro as this image), with
# the old apt path kept as the fallback.
#
# Base image pinned by digest (ubuntu:24.04 as published on Docker Hub).
FROM ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends ca-certificates curl dpkg-dev \
 && rm -rf /var/lib/apt/lists/*

# Package set before the media install, so the tar below is exactly the
# packages ffmpeg+tsduck pull in -- nothing from the base image.
RUN dpkg-query -W -f='${Package}\n' | sort -u > /tmp/pkgs-before

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# TSDuck is not in the Ubuntu archive (`apt-get install tsduck` has always
# failed on the runner -> the TS-relay splice test skips; see the comment in
# scripts/ci/install_media_test_prerequisites.sh). TSDuck publishes a
# per-Ubuntu .deb; ubuntu24 == this image's base. Pinned to one release so the
# URL is immutable.
ARG TSDUCK_DEB_URL=https://github.com/tsduck/tsduck/releases/download/v3.45-4798/tsduck_3.45-4798.ubuntu24_amd64.deb
RUN curl -fsSL -o /tmp/tsduck.deb "${TSDUCK_DEB_URL}" \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends /tmp/tsduck.deb \
 && rm -f /tmp/tsduck.deb \
 && rm -rf /var/lib/apt/lists/*

# Files owned by the packages we just added, as one gzipped tar (~1/3 the
# raw 485 MB, so the pull and the image layer stay small) the CI runner can
# extract over its own (identical) ubuntu-24.04 base. dpkg -L is the package
# database, so this cannot miss a shared library the way a hand-written file
# list would.
RUN dpkg-query -W -f='${Package}\n' | sort -u > /tmp/pkgs-after \
 && comm -13 /tmp/pkgs-before /tmp/pkgs-after > /tmp/pkgs-new \
 && mkdir -p /opt/ci-media \
 && dpkg-query -L $(cat /tmp/pkgs-new) \
      | sed -e 's|^/||' -e 's|/$||' | grep -v '^$' | sort -u \
      | while IFS= read -r path; do \
          if [ -e "/$path" ] || [ -L "/$path" ]; then printf '%s\0' "$path"; fi; \
        done > /tmp/files.list \
 && tar -czpf /opt/ci-media/rootfs.tar.gz --numeric-owner --no-recursion --null -C / -T /tmp/files.list \
 && tar -tzf /opt/ci-media/rootfs.tar.gz | sort > /tmp/tar-contents.txt \
 && grep -qx usr/bin/ffmpeg /tmp/tar-contents.txt \
 && grep -qx usr/bin/ffprobe /tmp/tar-contents.txt \
 && grep -qx usr/bin/tsp /tmp/tar-contents.txt \
 && ls -l /opt/ci-media/rootfs.tar.gz

# Build-time proof that the binaries in the tar actually run.
RUN ffmpeg -version | head -n 1 \
 && ffprobe -version | head -n 1 \
 && tsp --version
