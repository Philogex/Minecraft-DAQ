package dev.philogex.minecraftdaq.sampling;

import dev.philogex.minecraftdaq.recording.DaqRecorder;

public final class MouseDeltaCapture {
    private static DaqRecorder recorder;

    private MouseDeltaCapture() {
    }

    public static synchronized void register(DaqRecorder activeRecorder) {
        recorder = activeRecorder;
    }

    public static void record(double dx, double dy) {
        DaqRecorder activeRecorder;
        synchronized (MouseDeltaCapture.class) {
            activeRecorder = recorder;
        }
        if (activeRecorder != null) {
            activeRecorder.recordMouseDelta(dx, dy);
        }
    }
}
