package dev.philogex.minecraftdaq.recording;

public record MiningEventData(
    long eventTimeNs,
    int targetX,
    int targetY,
    int targetZ,
    String faceId,
    double hitX,
    double hitY,
    double hitZ,
    String blockStateBefore,
    String blockStateAfter,
    String neighborsJson
) {
}
