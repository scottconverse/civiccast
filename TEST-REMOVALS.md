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
