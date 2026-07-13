# Minecraft DAQ

Minecraft DAQ is a client-side Fabric mod for collecting movement and
interaction data from normal Minecraft gameplay.

The first target use case is mining-oriented mouse/camera trajectory logging.
Players should be able to play naturally while the mod records enough context to
reconstruct the final aim movement that led to a mined block.

## Status

This repository currently contains the first mining data-capture path:
recording commands, tick-based state sampling, raw mouse-delta sampling, and client-side block-break event
export.

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

`/daq stop` flushes and closes the active CSV files.

Recordings are written below the game directory:

```text
minecraft-daq/mining-<utc-time>-<session-prefix>/
```

Long recordings should use incremental writes. The implementation keeps small
in-memory ring buffers for recent state and mouse-delta samples and only exports
samples belonging to completed mining events.

The current sampling layer records tick-based state samples and raw
MouseHandler delta samples separately while a session is active. `/daq status`
reports both counters. When a client-side block-break event is observed, the
logger exports the recent state and mouse-delta windows to the active CSV
files.

While recording, a small HUD overlay shows the current event, state sample, and
mouse delta counts.

## Mining Dataset

The mining dataset is event-based. A mining event is created when the block
state of the currently relevant target block changes, for example from a block
to air. The logger then exports the previous time window from the ring buffer,
for example the last `1500 ms`.

The event does not need an explicit start timestamp. Start timing, reaction
time, and movement time should be reconstructed during analysis from the
exported sample window.

### CSV Shape

Each recording directory contains three CSV files.

`events.csv` contains one row per mined block event:

```csv
schema_version,
session_id,
event_id,
event_time_ns,
target_x,
target_y,
target_z,
face_id,
hit_x,
hit_y,
hit_z,
block_state_before,
block_state_after,
neighbors_json
```

`state_samples.csv` contains tick-based player and context samples for each
event window:

```csv
schema_version,
session_id,
event_id,
sample_time_ns,
event_time_ns,
relative_ms,
yaw,
pitch,
player_x,
player_y,
player_z,
fov,
gui_scale,
fps_estimate,
sensitivity
```

`mouse_trajectory.csv` contains raw MouseHandler deltas for each event window:

```csv
schema_version,
session_id,
event_id,
sample_time_ns,
event_time_ns,
relative_ms,
mouse_dx,
mouse_dy
```

### Field Notes

- `event_id` is unique within a session and identifies one mined block event.
- `sample_time_ns` should use a monotonic clock, preferably `System.nanoTime()`.
- `event_time_ns` is the timestamp of the observed block-state change.
- `relative_ms` is relative to `event_time_ns`, so samples before the event are
  negative.
- `mouse_dx` and `mouse_dy` are raw accumulated MouseHandler deltas for one
  MouseHandler movement handling interval.
- `yaw` and `pitch` are the resulting camera orientation for a state sample.
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

## Offline Analysis

The Python analysis tools live here rather than in Minescript-Miner so that
recorded human trajectories, reference datasets, and generated aim paths use
the same feature definitions.

`analysis/aim_features.py` contains the Fitts, submovement, and geometric
feature extraction. It only depends on the Python standard library and works
for both angular camera paths and screen-space cursor paths.

`tools/analyze_balabit_mouse.py` imports the public
`balabit/Mouse-Dynamics-Challenge` dataset once and writes `features.csv`, a
compact `summary.json`, and `paths.json.gz`. The last file is a deterministic
reservoir sample of target-aligned, resampled paths for cross-domain motion
plots. Later plots read these outputs instead of repeatedly processing the raw
dataset.

`tools/plot_aim_path.py` visualizes angular velocity, remaining target delta,
and the same feature table for one or more Minescript-Miner path generators.
It expects a sibling `Minescript-Miner` checkout by default; set
`MINESCRIPT_MINER_ROOT` when it lives elsewhere. Plotting additionally needs
matplotlib.

```bash
python tools/analyze_balabit_mouse.py --dataset ../Mouse-Dynamics-Challenge
python tools/plot_aim_path.py --generator sigmadrift \
  --reference-summary build/aim-analysis/balabit/summary.json
```

`analysis/mining_session.py` reads the three CSV files from one recorded
`mining-*` or `synthetic-*` directory and joins them by `event_id`. An optional
`metadata.json` marks synthetic recordings and documents derived or placeholder
fields. The first recording viewer uses that loader directly:

```bash
python tools/plot_mining_trajectory.py /path/to/mining-20260706-235935-62867abbad76 \
  --event-id 1
```

It writes `analysis/event-<id>.png` below the recording directory. The graph is
intentionally diagnostic for now: camera yaw/pitch, tick-derived angular
velocity, and raw mouse deltas before the block break. It does not yet infer a
target angle or compute final trajectory features.

### Grouped Sessions

The aggregate path, speed, feature, timeline, and cross-domain reference
plotters can combine multiple recording directories in memory without changing
or copying their CSV files. Use one `--dataset` per plotted method or cohort:

```bash
PYTHONPATH=../Minescript-Miner/src python tools/plot_path_density.py \
  --dataset Human /recordings/human-1 /recordings/human-2 \
  --dataset SigmaDrift /generated/sigma-1 /generated/sigma-2 \
  --output build/analysis/grouped-path-density.png
```

Each session is loaded and validated independently before its paths are added
to the group. Local `event_id` values may therefore overlap safely, generated
replicate weights remain unchanged, and the JSON report retains per-session
input counts and invalidation reasons below the aggregate group totals. Session
order is preserved by the concatenated timeline. The older positional syntax
remains available as a shorthand for one session per independently plotted
dataset; it must not be mixed with `--dataset` in the same command.

The face-hit plot has no method comparison axis. Supplying multiple positional
sessions pools all their hit points into its one distribution.

### Paired Generator Datasets

Generated reference datasets condition each path generator on the target
selected by a human DAQ event. They compare motion generation only: target
selection is deliberately held constant and is not evaluated by this dataset.

Install a Minescript-Miner wheel in the analysis environment, then run:

```bash
python tools/generate_reference_paths.py \
  /path/to/mining-session \
  /path/to/generated-sigmadrift-session \
  --generator sigmadrift \
  --replicates 5 \
  --config ../Minescript-Miner/aim_config.txt
```

The output is one DAQ-compatible batch session. Every generated event records
its source event, deterministic seed, replicate index, reconstructed target
width, and an analysis weight of `1 / replicate_count` in `metadata.json`.
Thus every human target condition contributes total weight one per generator.

By default, the generator detects the final stationary-player movement episode
inside the 1.5 s window and uses its onset as the generated path's initial
condition. The metadata records `detected_movement_onset`, all segmentation
parameters, and skipped-event counts. `--no-segmentation` retains the earlier
`window_first_sample` behavior for diagnostics.

### Path Density Comparison

`tools/plot_path_density.py` compares recorded and generated trajectories under
the same target conditions:

```bash
PYTHONPATH=../Minescript-Miner/src python tools/plot_path_density.py \
  /path/to/mining-session \
  /path/to/generated-session \
  --label Human \
  --label SigmaDrift \
  --output build/analysis/human-vs-sigmadrift-path-density.png
```

For a start-to-target movement vector `(dyaw, dpitch)`, the effective angular
target width is the projection of the reconstructed angular target rectangle
onto the movement axis:

```text
W_eff = sqrt((width_yaw * dyaw / D)^2 +
             (width_pitch * dpitch / D)^2)
D     = sqrt(dyaw^2 + dpitch^2)
```

Paths are rotated into a target-relative coordinate system and divided by `D`,
so every panel has `start=(0, 0)` and `target=(1, 0)`. Automatic `W_eff` strata
are weighted quantiles of the first session; use `--width-edges` for fixed,
cross-run boundaries. Every human event has weight one, while all stochastic
replicates of one generated event together have weight one. Every path also
contributes equal total mass to its density regardless of sample count.

The default viewport displays the central 95 percent weighted point mass with
shared limits and color scaling inside each stratum. Panel titles report the
visible fraction, medians of `W_eff`, Fitts ID, and the reconstructed yaw/pitch
widths. Viewport clipping never removes paths from the statistics.

Before plotting human data, the analyzer separates active camera intervals from
quantized still samples, bridges short correction pauses, and selects the most
recent episode that has sufficient amplitude, approaches the target, and ends
inside its reconstructed angular extent. Hold time after aiming and motion
toward previous blocks are removed. Events whose player position changes by
more than `0.05` blocks during the episode are excluded from the stationary
cohort. Parameters are exposed by the CLI, and a JSON report next to the PNG
records the configuration plus every invalidation reason and count.

### Speed Density Comparison

`tools/plot_speed_density.py` uses the same target reconstruction, movement
segmentation, event weighting, and `W_eff` stratification as the spatial path
plot:

```bash
PYTHONPATH=../Minescript-Miner/src python tools/plot_speed_density.py \
  /path/to/mining-session \
  /path/to/generated-session \
  --label Human \
  --label SigmaDrift \
  --output build/analysis/human-vs-sigmadrift-speed-density.png
```

The x axis is normalized movement time from zero to one; the y axis retains
physical angular speed in degrees per second. Each piecewise speed profile is
interpolated onto the same time grid before aggregation. Consequently, a
generator does not receive more density weight merely because it emits more
samples than the 20 Hz DAQ state stream. Each path contributes equal total mass,
and generated replicates retain their `1 / replicate_count` weights.

The cyan curve is the weighted median speed across paths at each normalized
time position. Density and median therefore retain dwell-time and velocity
structure while remaining comparable across different movement durations. The
shared y limit in each stratum defaults to the pooled 99th weighted speed
percentile; the exact in-viewport fraction is shown and written to the adjacent
JSON report.

### Kinematic Feature Distributions

`tools/plot_feature_distributions.py` replaces the earlier single-path feature
table with weighted, shared-bin histograms and median markers:

```bash
PYTHONPATH=../Minescript-Miner/src python tools/plot_feature_distributions.py \
  /path/to/mining-session \
  /path/to/generated-session \
  --label Human \
  --label SigmaDrift \
  --output build/analysis/human-vs-sigmadrift-feature-distributions.png
```

The comparison implements the paper's complete clean set of 17 features: four
Fitts features, five submovement features, four smoothness features, and four
geometry features. The smoothness family (`smooth_jerk_rms`,
`smooth_norm_jerk`, `smooth_ldlj`, and
`smooth_curvature_change_rate`) follows the public
`ck0i/sigmadrift-detector` formulas. Human smoothness and fine geometry use the
high-rate raw mouse deltas reconstructed inside the tick-detected movement
episode; state samples continue to define onset, player position, and target
geometry. Because no pre-delta mouse timestamp exists, the initial orientation
sample is placed one local median mouse interval before the first delta (clamped
to `4..50 ms`). This prevents an arbitrary sub-millisecond tick/frame offset
from creating a false first-sample velocity and jerk spike.

The plotted feature names have the following meanings:

- `fitts_mt`: observed movement time in milliseconds.
- `fitts_id`: Fitts index of difficulty, `log2(D / W_eff + 1)`.
- `fitts_residual`: observed minus Fitts-predicted movement time.
- `fitts_residual_ratio`: residual divided by predicted movement time.
- `sub_peak_count`: number of speed peaks above the relative peak threshold.
- `sub_primary_amp_ratio`: amplitude reached at the primary speed peak, divided by `D`.
- `sub_correction_onset`: estimated first correction onset time in milliseconds.
- `sub_interpeak_cv`: coefficient of variation of intervals between speed peaks.
- `sub_peak_speed_ratio`: secondary-to-primary peak-speed ratio.
- `smooth_jerk_rms`: root-mean-square magnitude of trajectory jerk.
- `smooth_norm_jerk`: dimensionless jerk normalized by duration and amplitude.
- `smooth_ldlj`: log dimensionless jerk; larger values indicate smoother motion.
- `smooth_curvature_change_rate`: RMS time derivative of path curvature.
- `geo_path_efficiency`: straight-line distance divided by traveled path length.
- `geo_max_deviation`: maximum perpendicular distance from the direct path.
- `geo_angular_dev_at_peak`: heading error at maximum movement speed, in degrees.
- `geo_curvature_integral`: accumulated absolute heading change along the path.

All datasets use identical bin edges per feature. Histograms show weighted
counts rather than raw sample counts, and dashed lines plus legend values show
weighted medians. Non-finite values are excluded per feature and their missing
weight is retained in the adjacent JSON report. The default viewport covers the
central 99 percent pooled weighted value mass without changing medians or other
reported statistics.

An external feature reference can be added to the same figure:

```bash
PYTHONPATH=../Minescript-Miner/src python tools/plot_feature_distributions.py \
  /path/to/mining-session \
  /path/to/generated-session \
  --label Human \
  --label SigmaDrift \
  --reference-features build/aim-analysis/balabit/features.csv \
  --reference-label Balabit \
  --output build/analysis/human-vs-sigmadrift-vs-balabit-features.png
```

Balabit uses screen pixels and has no recorded target geometry. Its
`fitts_id`, residuals, raw jerk RMS, curvature-change rate, and absolute maximum
deviation are therefore omitted from the shared figure. Movement time,
submovement ratios/counts, dimensionless jerk, path efficiency, angular heading
deviation, and curvature integral remain comparable. Balabit's pause-separated
segment endpoint is only a proxy for user intent, not an observed UI target.

### Cross-Domain Motion Reference

`tools/plot_motion_reference.py` compares path and speed shape without mixing
Minecraft degrees and Balabit pixels:

```bash
PYTHONPATH=../Minescript-Miner/src python tools/plot_motion_reference.py \
  /path/to/mining-session \
  /path/to/generated-session \
  --label Human \
  --label SigmaDrift \
  --reference-paths build/aim-analysis/balabit/paths.json.gz \
  --reference-label Balabit \
  --output build/analysis/human-vs-sigmadrift-vs-balabit-motion.png
```

Every path is rotated and scaled to `start=(0, 0)` and
`target/segment-endpoint=(1, 0)`. Speed is divided by `D / movement_time`, so
the lower panels describe temporal speed shape rather than degrees or pixels
per second. No `W_eff` stratification is applied because Balabit contains no
target width. The default spatial viewport contains the central 95 percent
weighted point mass; omitted tails remain represented in the JSON report.

### Face Hit Distribution

`tools/plot_face_hit_distribution.py` shows where the final raycast intersects
each of the six target-block faces:

```bash
python tools/plot_face_hit_distribution.py /path/to/mining-session \
  --output build/analysis/face-hit-distribution.png
```

Hit coordinates are translated into block-local `[0, 1]` coordinates while
retaining the corresponding world axes: north/south use `(x, y)`, east/west
use `(z, y)`, and up/down use `(x, z)`. Opposite faces are deliberately not
mirrored. Density is normalized independently per face so that spatial bias on
rare faces remains visible; titles separately report each face's absolute count
and share of all valid hits. The cyan cross marks the median hit coordinate.

### Concatenated Movement Timeline

`tools/plot_concatenated_timeline.py` places every valid segmented movement
episode directly after the previous one:

```bash
PYTHONPATH=../Minescript-Miner/src python tools/plot_concatenated_timeline.py \
  /path/to/mining-session \
  /path/to/generated-session \
  --label Human \
  --label SigmaDrift \
  --output build/analysis/human-vs-sigmadrift-timeline.png
```

The plot removes real-world idle gaps between mining events but preserves every
episode's physical duration. One panel shows angular speed in degrees per
second; the other shows remaining angular target delta divided by the original
start-to-target distance. Line breaks and faint separators identify event
boundaries. This view is intended to expose repeated temporal shapes,
correction phases, and generator resets that aggregate density plots can hide.

## Future Datasets

The project should stay task-oriented, but only mining is planned for the first
implementation. Future data tasks may include movement, pathing, combat, or
general interaction datasets.
