package dev.philogex.minecraftdaq.recording;

import java.util.ArrayList;
import java.util.List;

public final class MouseDeltaRingBuffer {
    private final MouseDeltaSample[] samples;
    private int nextIndex;
    private int size;

    public MouseDeltaRingBuffer(int capacity) {
        if (capacity <= 0) {
            throw new IllegalArgumentException("capacity must be positive");
        }
        this.samples = new MouseDeltaSample[capacity];
    }

    public void add(MouseDeltaSample sample) {
        samples[nextIndex] = sample;
        nextIndex = (nextIndex + 1) % samples.length;
        if (size < samples.length) {
            size++;
        }
    }

    public int size() {
        return size;
    }

    public int capacity() {
        return samples.length;
    }

    public void clear() {
        for (int index = 0; index < size; index++) {
            samples[index] = null;
        }
        nextIndex = 0;
        size = 0;
    }

    public List<MouseDeltaSample> recentSince(long cutoffTimeNs) {
        List<MouseDeltaSample> out = new ArrayList<>();
        for (int offset = size; offset > 0; offset--) {
            int index = nextIndex - offset;
            if (index < 0) {
                index += samples.length;
            }
            MouseDeltaSample sample = samples[index];
            if (sample != null && sample.sampleTimeNs() >= cutoffTimeNs) {
                out.add(sample);
            }
        }
        return out;
    }
}
