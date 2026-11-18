# SPDX-License-Identifier: Apache-2.0
#
# Ready-made docs toolchain for .github/workflows/ci-docs.yml.
#
# Why: on 2026-11-18 the Ubuntu archive was slow for ~2 hours (20:27-22:17Z)
# and "Install pandoc and TeX (per ADR 0005)" sat in a 10-minute step timeout.
# On a normal day the same apt call takes 23-63 s. This image is the same apt
# list, installed once at image build time, so the CI job never waits on
# archive.ubuntu.com.
#
# NOT pandoc/latex (Alpine): the docs job runs `uv sync` and a Python 3.12
# toolchain, which the Alpine image cannot host. Ubuntu 24.04 matches the
# runner the job used before (ubuntu-latest at the time of writing).
#
# Base image pinned by digest (ubuntu:24.04 as published on Docker Hub).
FROM ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8

ENV DEBIAN_FRONTEND=noninteractive

# The exact apt list the workflow used to run, plus what the job needs on top
# of a bare Ubuntu rootfs:
#   - git/ca-certificates/curl/xz-utils/tar: actions/checkout and
#     astral-sh/setup-uv inside a container job.
#   - build-essential/pkg-config/libssl-dev/libffi-dev: `uv sync --all-extras
#     --group dev` builds any dependency that has no manylinux wheel.
#   - python3/python3-venv: the job's own setup-python step is kept, but a
#     working system interpreter keeps every incidental `python3` call valid.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      pandoc \
      lmodern \
      texlive-xetex \
      texlive-fonts-recommended \
      texlive-latex-recommended \
      texlive-latex-extra \
      git \
      ca-certificates \
      curl \
      xz-utils \
      tar \
      gzip \
      build-essential \
      pkg-config \
      libssl-dev \
      libffi-dev \
      python3 \
      python3-venv \
      python3-dev \
 && rm -rf /var/lib/apt/lists/*

# Build-time proof that the toolchain the workflow needs is actually present.
# If any of these disappear, the image build fails instead of the CI job.
RUN pandoc --version | head -n 1 \
 && xelatex --version | head -n 1 \
 && git --version \
 && python3 --version \
 && tex --version | head -n 1
