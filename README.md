# Minecraft DAQ

Minecraft DAQ is a client-side Fabric mod for collecting movement and
interaction data from normal Minecraft gameplay.

The first target use case is mining-oriented mouse/camera trajectory logging.
Players should be able to play naturally while the mod records enough context to
reconstruct the final aim movement that led to a mined block.

## Status

This repository currently contains the Fabric project skeleton only. The data
logger, commands, and event matching are intentionally not implemented yet.

## Build

The mod uses Fabric Loom and keeps all version pins in `gradle.properties`.

```bash
./gradlew build
```

The initial target version is Minecraft `26.2` with Fabric Loader `0.19.3`.
If a test instance uses a different version, change `minecraft_version` and
`loader_version` in `gradle.properties`.

Minecraft `26.1` and newer are shipped unobfuscated. The build therefore uses
the `net.fabricmc.fabric-loom` plugin id for non-obfuscated Minecraft versions
and does not declare a separate `mappings` dependency.

## CI

GitHub Actions verifies the project on pushes, pull requests, and manual
workflow runs. The workflow is defined in `.github/workflows/build.yml`.

Release artifacts are built only for tags matching `vX.X.X`, for example
`v0.1.0`. Tagged builds upload the generated JAR files from `build/libs/` as a
short-lived artifact.

The CI path uses `gradle/actions/setup-gradle` to provide Gradle even before a
project-local Gradle Wrapper is committed.

## Data Model

Recording is planned around explicit start/stop commands:

```text
/daq start
/daq stop
/daq status
```

`/daq start` creates a new anonymous session id. The id is generated from a
random UUID plus current wall-clock and monotonic timestamps, then hashed with
SHA-256. Participant ids can be assigned later during analysis and do not need
to be stored directly by the mod.

`/daq stop` flushes and closes the active CSV file.

Recordings are written below the game directory:

```text
minecraft-daq/mining-<utc-time>-<session-prefix>.csv
```

Long recordings should use incremental writes. The implementation should keep a
small in-memory ring buffer for recent samples and only export samples belonging
to completed mining events.

The current sampling layer records into an in-memory ring buffer while a
session is active. `/daq status` reports total, tick, and frame sample counts so
the sampling hooks can be verified in-game before event export is added.

## Mining Dataset

The mining dataset is event-based. A mining event is created when the block
state of the currently relevant target block changes, for example from a block
to air. The logger then exports the previous time window from the ring buffer,
for example the last `1500 ms`.

The event does not need an explicit start timestamp. Start timing, reaction
time, and movement time should be reconstructed during analysis from the
exported sample window.

### CSV Shape

The first implementation should use a single CSV file. Event metadata is
duplicated on each sample row so the file can be analyzed by grouping on
`event_id`.

Planned columns:

```csv
schema_version,
session_id,
event_id,
sample_time_ns,
event_time_ns,
relative_ms,
mouse_dx,
mouse_dy,
yaw,
pitch,
player_x,
player_y,
player_z,
target_x,
target_y,
target_z,
face_id,
hit_x,
hit_y,
hit_z,
block_state_before,
block_state_after,
neighbors_json,
fov,
gui_scale,
fps_estimate,
sensitivity
```

### Field Notes

- `event_id` is unique within a session and identifies one mined block event.
- `sample_time_ns` should use a monotonic clock, preferably `System.nanoTime()`.
- `event_time_ns` is the timestamp of the observed block-state change.
- `relative_ms` is relative to `event_time_ns`, so samples before the event are
  negative.
- `mouse_dx` and `mouse_dy` should be raw or lowest-level available mouse deltas.
- `yaw` and `pitch` are the resulting camera orientation for the sample.
- `target_x`, `target_y`, and `target_z` are the block coordinates of the mined
  block.
- `face_id` is the hit face from the most recent matching raycast result, if
  available.
- `hit_x`, `hit_y`, and `hit_z` are the exact raycast hit position, if
  available.
- `neighbors_json` stores the 26 neighboring block states around the target
  block at event time. This is intentionally serialized inside one CSV field so
  the dataset can evolve without adding many fixed columns.
- `fov`, `gui_scale`, `fps_estimate`, and `sensitivity` are context values. They
  should be logged for normalization and filtering, but not directly mixed into
  the raw trajectory features.

### Neighbor Object

`neighbors_json` should contain offsets relative to the target block:

```json
[
  {"dx": -1, "dy": -1, "dz": -1, "state": "minecraft:stone"},
  {"dx": -1, "dy": -1, "dz": 0, "state": "minecraft:air"}
]
```

The center block is omitted because it is already represented by
`block_state_before` and `block_state_after`.

## Future Datasets

The project should stay task-oriented, but only mining is planned for the first
implementation. Future data tasks may include movement, pathing, combat, or
general interaction datasets.
