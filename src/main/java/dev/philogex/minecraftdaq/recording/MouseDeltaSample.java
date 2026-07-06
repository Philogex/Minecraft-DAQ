package dev.philogex.minecraftdaq.recording;

public record MouseDeltaSample(
    long sampleTimeNs,
    double mouseDx,
    double mouseDy
) {
}
