#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# scripts/ci/install_media_test_prerequisites.sh
#
# Shared "install ffmpeg + tsduck for the media test suites" step for
# hosted-ubuntu CI jobs -- ci-test.yml's `Unit tests` job and
# deterministic-detectors.yml's `randomized-suite` job. Both jobs used to
# carry a near-identical copy of this shell inline; consolidated here so a
# fix lands once instead of drifting between copies.
#
# Two documented hosted-runner failure modes, both bounded below:
#
#   1. unattended-upgrades / apt-daily*.timer holds the dpkg/apt locks on
#      boot. Bare `apt-get` waits on that lock FOREVER: on 2026-08-19 this
#      step sat in_progress for 3h49m on the release branch and ~2h on PRs
#      #422/#423, silently wedging the whole merge queue while every other
#      job showed green.
#   2. The Azure-hosted mirror (azure.archive.ubuntu.com) occasionally
#      serves a handful of packages at a few hundred KB/s instead of its
#      usual multi-MB/s, or the connection itself stalls outright. Confirmed
#      across four independent runs that never touched the dpkg lock at all:
#        - PR #131 "Unit tests" job 100278667553 (libflite1, 13.6 MB,
#          stalled from 14:08:54 past the old 300s per-call bound)
#        - PR #135 "randomized-suite" job 100284786583 (libdav1d7, stalled
#          from 14:20:16 to 14:24:41 -- 4m25s for a 604 kB package)
#        - PR #132 "randomized-suite" job 100287702253 (same pattern)
#        - PR #136 "randomized-suite" job 100314970674, AFTER the 300s->480s
#          per-call raise above: `Get:10 ... libdav1d7 ... [604 kB]` logged
#          at 15:46:13Z, then nothing until the 480s `timeout` killed it at
#          15:50:00Z. `Acquire::Retries` never fired -- it only retries a
#          call that actually fails or completes; a socket that goes silent
#          mid-transfer without erroring or finishing is invisible to it.
#          Fixed by giving apt its own low-level stall detector
#          (Acquire::http::Timeout / Acquire::https::Timeout) so a silent
#          connection aborts in 30s and the retry logic actually gets a
#          chance to run, plus a mirror-swap fallback in case the Azure
#          mirror itself is the problem rather than one bad connection.
#
# Bound both causes: stop the background upgrader before touching apt, wait
# for the dpkg/apt locks with a visible bounded loop, bound the network at
# both the per-call and per-connection level, fall back to a different
# mirror if the primary one is unhealthy, and give the whole step enough
# headroom to survive a slow mirror -- but not forever. On failure, dump
# what's still holding apt/dpkg so the next failure is diagnosable from the
# log alone.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

LOCK_WAIT_SECS="${APT_LOCK_WAIT_SECS:-180}"
APT_TIMEOUT_SECS="${APT_CALL_TIMEOUT_SECS:-480}"
# Acquire::Retries only retries a call that fails or finishes -- it does
# nothing for a connection that goes silent mid-transfer without either. The
# http/https ::Timeout options are the actual stall detector: no bytes for
# 30s on a given connection aborts it, which THEN gives Acquire::Retries
# something to retry. Dl-Limit=0 explicitly means "no artificial throughput
# cap" (the default), stated here so a slow mirror is never self-inflicted.
APT=(
  sudo -n apt-get
  -o DPkg::Lock::Timeout=120
  -o Acquire::Retries=3
  -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30
  -o Acquire::http::Dl-Limit=0
)

echo "::group::Stopping the runner's background apt/dpkg holders"
# unattended-upgrades and the apt-daily* timers/services are what held the
# dpkg lock for 3h49m on 2026-08-19. Stop and kill them defensively before
# ever touching apt ourselves; `|| true` throughout because a hosted image
# that has already finished (or never started) these units must not fail
# this step -- their absence is success, not an error.
sudo -n systemctl stop unattended-upgrades apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
sudo -n systemctl kill --kill-who=all apt-daily.service apt-daily-upgrade.service >/dev/null 2>&1 || true
echo "::endgroup::"

echo "::group::Waiting for dpkg/apt locks (bounded to ${LOCK_WAIT_SECS}s)"
waited=0
while sudo -n fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; do
  if [ "$waited" -ge "$LOCK_WAIT_SECS" ]; then
    echo "::warning::dpkg/apt locks still held after ${LOCK_WAIT_SECS}s; proceeding anyway -- apt-get's own DPkg::Lock::Timeout=120 makes the final call."
    break
  fi
  echo "Locks still held after ${waited}s, holder(s):"
  sudo -n fuser -v /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock 2>&1 || true
  sleep 5
  waited=$((waited + 5))
done
echo "::endgroup::"

report_apt_diagnostics() {
  echo "::group::apt/dpkg diagnostics (step failed)"
  ps -ef | grep -E 'apt|dpkg|unattended' | grep -v grep || true
  echo "::endgroup::"
}
trap report_apt_diagnostics ERR

# Mirror-swap fallback (PR #136 job 100314970674 root cause): the 30s
# stall timeout above makes a hung connection fail fast, but if the Azure
# mirror itself is unhealthy that just means every retry against it fails
# fast too. Swap to a different mirror and re-run `apt-get update` against
# it before giving install another try. CURRENT_APT_HOST tracks what the
# sources currently point at so each swap's sed targets the right string.
CURRENT_APT_HOST="azure.archive.ubuntu.com"

switch_apt_mirror() {
  local new_host="$1"
  echo "::group::Switching apt mirror: ${CURRENT_APT_HOST} -> ${new_host}"
  sudo -n sed -i "s|${CURRENT_APT_HOST}|${new_host}|g" \
    /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true
  CURRENT_APT_HOST="$new_host"
  timeout "$APT_TIMEOUT_SECS" "${APT[@]}" update
  echo "::endgroup::"
}

# apt_install_with_mirror_fallback <apt-get install args...>
# Attempt 1 against whatever mirror is already configured (the hosted
# runner's default, azure.archive.ubuntu.com); on failure, fall back to
# archive.ubuntu.com, then mirrors.edge.kernel.org/ubuntu. Prints which
# mirror the install actually succeeded on.
apt_install_with_mirror_fallback() {
  local original_host="$CURRENT_APT_HOST"
  if timeout "$APT_TIMEOUT_SECS" "${APT[@]}" install "$@"; then
    echo "::notice::apt install succeeded via mirror: ${CURRENT_APT_HOST}"
    return 0
  fi
  local fallback_host
  for fallback_host in archive.ubuntu.com mirrors.edge.kernel.org/ubuntu; do
    echo "::warning::apt install failed via ${CURRENT_APT_HOST}; falling back to ${fallback_host}"
    if switch_apt_mirror "$fallback_host" && timeout "$APT_TIMEOUT_SECS" "${APT[@]}" install "$@"; then
      echo "::notice::apt install succeeded via mirror: ${CURRENT_APT_HOST}"
      return 0
    fi
  done
  echo "::error::apt install failed on every mirror attempted (default ${original_host}, archive.ubuntu.com, mirrors.edge.kernel.org/ubuntu)."
  return 1
}

# ---------------------------------------------------------------------------
# Ready-made image path (2026-11-18).
#
# The apt calls above are the slow-archive failure mode that hung CI for ~2
# hours on 2026-11-18. .github/ci-images/media.Dockerfile builds an
# ubuntu-24.04 image with ffmpeg + TSDuck already installed, plus
# /opt/ci-media/rootfs.tar: exactly the files those packages own (dpkg -L of
# the packages the install added). Extracting that tar over the runner's own
# identical ubuntu-24.04 base installs the media stack with no archive access
# at all, typically in a few seconds.
#
# Why extract instead of `container:`: the jobs that call this script run
# testcontainers against a real Postgres and need the runner's Docker socket
# (ci-test.yml -- CIVICCAST_RUN_POSTGRES_TESTS=1, TEST-001). A GitHub container
# job does not get the runner's Docker daemon, so containerizing them would
# silently break that proof.
#
# Fallback: if the image cannot be pulled (registry outage, branch that has not
# built the image yet) or the extracted binaries do not run, this returns
# non-zero and the caller falls through to the original apt path below.
# ---------------------------------------------------------------------------
CI_MEDIA_IMAGE="${CIVICAST_CI_MEDIA_IMAGE:-ghcr.io/scottconverse/civiccast-ci-media@sha256:PENDING_DIGEST}"

provision_media_from_image() {
  command -v docker >/dev/null 2>&1 || { echo "docker not available"; return 1; }

  if [ -n "${GHCR_TOKEN:-}" ]; then
    echo "$GHCR_TOKEN" | docker login ghcr.io \
      -u "${GHCR_USER:-github-actions}" --password-stdin >/dev/null 2>&1 \
      || echo "::warning::ghcr.io login failed; continuing (the image may be public)"
  fi

  echo "::group::Pulling ${CI_MEDIA_IMAGE} (3 attempts)"
  local attempt ok=0
  for attempt in 1 2 3; do
    if timeout 600 docker pull "$CI_MEDIA_IMAGE"; then
      ok=1
      break
    fi
    echo "::warning::attempt ${attempt} to pull ${CI_MEDIA_IMAGE} failed"
    sleep $((attempt * 15))
  done
  echo "::endgroup::"
  [ "$ok" = 1 ] || return 1

  local cid
  cid=$(docker create "$CI_MEDIA_IMAGE") || return 1
  if ! docker cp "$cid:/opt/ci-media/rootfs.tar" /tmp/ci-media-rootfs.tar; then
    docker rm -f "$cid" >/dev/null 2>&1 || true
    return 1
  fi
  docker rm -f "$cid" >/dev/null 2>&1 || true

  echo "::group::Installing media stack from the image"
  # tar runs over the identical ubuntu-24.04 base; the archive holds only
  # files owned by the ffmpeg/TSDuck packages.
  if ! sudo -n tar -xpf /tmp/ci-media-rootfs.tar -C /; then
    echo "::endgroup::"
    rm -f /tmp/ci-media-rootfs.tar
    return 1
  fi
  rm -f /tmp/ci-media-rootfs.tar
  echo "::endgroup::"

  # Prove the extracted binaries run before trusting them.
  command -v ffmpeg >/dev/null 2>&1 \
    && command -v ffprobe >/dev/null 2>&1 \
    && ffmpeg -version >/dev/null 2>&1 \
    && ffprobe -version >/dev/null 2>&1
}

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if provision_media_from_image; then
    echo "::notice::ffmpeg/ffprobe provisioned from ${CI_MEDIA_IMAGE} (no apt used)"
  else
    echo "::warning::ready-made media image unavailable; falling back to apt"
    timeout "$APT_TIMEOUT_SECS" "${APT[@]}" update
    apt_install_with_mirror_fallback -y ffmpeg
  fi
fi

# #151 TS-relay behavioral splice test (skips without tsp).
#
# GauntletGate T5: this tolerates a failed install with a warning and has NO
# junit-floor guard, unlike the suites gated on ffmpeg above. That is a
# DELIBERATE choice, not an oversight: TS-relay splice compliance belongs to
# the deferred `civiccast-cable` add-on (CLAUDE.md, closed architectural
# decisions), not the WSL lane's ship-critical streaming core. Cable-add-on
# CI coverage is intentionally best-effort until the add-on itself is in
# scope. If cable ships, this needs a floor guard matching the ffmpeg
# pattern above.
if ! command -v tsp >/dev/null 2>&1; then
  timeout "$APT_TIMEOUT_SECS" "${APT[@]}" install -y tsduck || echo '::warning::tsduck not installable; TS-relay splice test will skip (best-effort by design; see comment)'
fi

trap - ERR

ffmpeg -version | head -n 1
ffprobe -version | head -n 1
