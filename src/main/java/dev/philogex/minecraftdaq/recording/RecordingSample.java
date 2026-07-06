package dev.philogex.minecraftdaq.recording;

public record RecordingSample(
    long sampleTimeNs,
    float yaw,
    float pitch,
    double playerX,
    double playerY,
    double playerZ,
    int fov,
    int guiScale,
    int fpsEstimate,
    double sensitivity
) {
}
