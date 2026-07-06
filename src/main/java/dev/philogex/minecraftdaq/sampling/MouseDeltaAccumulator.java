package dev.philogex.minecraftdaq.sampling;

public final class MouseDeltaAccumulator {
    private static double totalDx;
    private static double totalDy;

    private MouseDeltaAccumulator() {
    }

    public static synchronized void add(double dx, double dy) {
        totalDx += dx;
        totalDy += dy;
    }

    public static synchronized Snapshot snapshot() {
        return new Snapshot(totalDx, totalDy);
    }

    public record Snapshot(double totalDx, double totalDy) {
    }
}
