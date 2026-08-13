package dev.philogex.minecraftdaq.recording;

public record MiningStartData(
    long startTimeNs,
    float destroyProgressPerTick,
    Integer expectedBreakTicks,
    String neighborsJson,
    String worldSnapshotJson
) {
}
