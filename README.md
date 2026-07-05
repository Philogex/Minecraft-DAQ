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

If the Gradle wrapper has not been generated yet, use a local Gradle
installation once:

```bash
gradle wrapper
./gradlew build
```

The initial target version is Minecraft `26.2` with Fabric Loader `0.19.2`.
If a test instance still uses `26.1.2`, change `minecraft_version`,
`fabric_api_version`, and possibly `loom_version` in `gradle.properties`.

## Data Model

Recording is planned around explicit start/stop commands:

```text
/data start
/data stop
```

`/data start` creates a new anonymous session id. The id should be generated
from a random value plus the current timestamp, then hashed with SHA-256.
Participant ids can be assigned later during analysis and do not need to be
stored directly by the mod.

`/data stop` should flush and close the active CSV file.

Long recordings should use incremental writes. The implementation should keep a
small in-memory ring buffer for recent samples and only export samples belonging
to completed mining events.

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
