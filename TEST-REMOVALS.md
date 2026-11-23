# Removed tests

A test whose root cause cannot be found is deleted, not skipped or retried.
Each entry names the test, the date, what was tried, and where the run is.

## 2026-11-19 — rc.6.1: live captions default flipped to ON

Owner decision 2026-11-15 (PR #350): live captions are on by default, and a
station that saved "off" under an older build is turned on once at upgrade.
Three tests asserted the old default, so they could not survive the change.
Each was rewritten in place (same module, same fixtures, inverted assertions
plus the new migration cases) rather than deleted; the old names are recorded
here so the rename is visible.

| Old test | Replaced by | Why | Run |
|---|---|---|---|
| `tests/egress/test_gst_strategy.py::test_strategy_builds_no_caption_embed_leg_on_a_fresh_station_by_default` | `test_strategy_builds_the_caption_embed_leg_on_a_fresh_station_by_default` | A fresh station now resolves to ON, so the embed leg (`cccombiner`) and the audio tap are built. Same body; the three assertions were inverted. | `pytest tests/egress/test_gst_strategy.py` (PR #350) |
| `tests/platform/test_station_router_api.py::test_live_captions_are_off_by_default_and_can_be_switched_on` | `test_live_captions_are_on_by_default_and_can_be_switched_off` | Same switch, new default; the test now proves it can be turned off. | `pytest tests/platform/test_station_router_api.py` (PR #350) |
| `tests/platform/test_station_router_api.py::test_an_absent_key_rehydrates_as_the_beta5_default_off` | `test_an_absent_key_rehydrates_as_the_default_on` | An absent key rehydrates as the shipped default, which is now ON. | `pytest tests/platform/test_station_router_api.py` (PR #350) |

The behaviour the old tests guarded is still guarded, in the new direction, by
those three tests plus three new ones added alongside them
(`test_an_older_build_saved_off_is_turned_on_once`,
`test_off_saved_after_the_upgrade_stays_off`,
`test_env_off_still_wins_over_a_migrated_on`).

## 2026-11-20 — U63: the audio PES PTS step is fixed, so its acceptance is gone

Owner acceptance of 2026-10-29 let exactly one media failure pass required CI:
one backward audio PID 66 PES PTS step (`{66: 1}`) in
`test_immediate_finite_playlist_reload_holds_rebases_and_stays_on_air`, and only
for `1.0.0-rc.1`. The product now holds the rebased changeover segment at the
audio mux sink pad until that pad has given up the retiring leg's queued
buffers, so no stale audio is re-dated into the incoming timeline and the step
cannot be emitted. With the defect gone, the acceptance had to go with it: the
verifier accepts no media failure at all.

| Old test | Replaced by | Why | Run |
|---|---|---|---|
| `tests/policy/test_native_caption_workflow_policy.py::test_windows_media_owner_acceptance_keeps_the_failed_junit` | `test_windows_media_junit_rejects_every_failure` | The rc.1-only acceptance branch is deleted from `scripts/verify_windows_media_junit.py`; the exact former accepted JUnit is now asserted to raise. | `pytest tests/policy/test_native_caption_workflow_policy.py` |
| `tests/policy/test_native_caption_workflow_policy.py::test_windows_media_owner_acceptance_rejects_other_outcomes` | `test_windows_media_junit_rejects_every_failure` | Same rejection matrix (identities, observed result, source assertion, skips, errors, duplicates, incomplete execution, every nonstandard exit) kept in one test, with the workflow's acceptance wording asserted absent. | `pytest tests/policy/test_native_caption_workflow_policy.py` |

Net effect: two policy tests became one. No `tests/native` case changed, so the
`ci-test.yml` junit floors (2215 pure / 2487 Windows) still match.

## 2026-11-21 — rc.8 MSI round 3: a warm is no longer refused for a short runway

`tests/egress/test_preparer.py::test_a_plan_warm_that_cannot_finish_before_airtime_fails_once_quickly`
asserted the round-2 refusal: a plan warm was skipped when its airing had
started or the design-speed cost exceeded the runway to it. That rule made
every window of a long programme cold, so it was removed.

| Old test | Replaced by | Why | Run |
|---|---|---|---|
| `test_a_plan_warm_that_cannot_finish_before_airtime_fails_once_quickly` | `test_a_long_programme_already_airing_is_still_warmed_for_its_later_windows` and `test_a_warm_that_cannot_finish_before_airtime_still_runs_for_later_airings` | Both cases now assert the warm runs (a whole-asset cache entry serves the programme's later 1800 s windows) and that a started airing sorts behind every future one. | `pytest tests/egress/test_preparer.py` |

## 2026-11-21 — rc.8 MSI round 4: the deferred switch is EOS-driven again

Round 3 sent a deferred rollover's plan end as `switch_at_unix`, a field that
means "switch at this wall-clock time". Two tests pinned that shape. Both were
replaced in place with tests that pin the real contract: the outgoing leg's
EOS drives the commit, and the plan end only delays the 900 s no-EOS watchdog.

| Old test | Replaced by | Why | Run |
|---|---|---|---|
| `tests/egress/test_daemon.py::test_a_deferred_rollover_gives_the_engine_its_boundary` | `test_a_deferred_rollover_delays_only_the_watchdog_and_never_the_switch` | An ordinary deferred rollover must send no `switch_at_unix`; it sends `defer_watchdog_not_before_unix` at plan end + 60 s instead. | `pytest tests/egress/test_daemon.py` |
| `tests/egress/test_gst_engine_reload_commit_ordering.py::test_a_lead_longer_than_the_defer_watchdog_cannot_cut_the_program_early` | `test_the_watchdog_fires_at_the_later_of_900s_and_the_boundary_plus_60s` (plus `test_an_old_leg_eos_before_the_boundary_commits_at_the_eos`, `test_a_stalled_old_leg_before_the_boundary_is_still_rescued`, `test_a_request_with_no_defer_watchdog_field_behaves_as_before`) | The old test proved a `switch_at_unix` guard; the new shape proves the watchdog fires at the later of ready+900 s and boundary+60 s, while an early EOS and the stall rescue are unaffected. | `pytest tests/egress/test_gst_engine_reload_commit_ordering.py` |
