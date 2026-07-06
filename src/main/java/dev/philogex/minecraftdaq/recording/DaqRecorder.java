package dev.philogex.minecraftdaq.recording;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

public final class DaqRecorder {
    private static final int SCHEMA_VERSION = 1;
    private static final int STATE_RING_BUFFER_CAPACITY = 8192;
    private static final int MOUSE_RING_BUFFER_CAPACITY = 8192;
    private static final long EVENT_WINDOW_NS = 1_500_000_000L;
    private static final DateTimeFormatter FILE_TIME_FORMAT =
        DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").withZone(ZoneOffset.UTC);
    private static final String EVENTS_CSV_HEADER = String.join(
        ",",
        "schema_version",
        "session_id",
        "event_id",
        "event_time_ns",
        "target_x",
        "target_y",
        "target_z",
        "face_id",
        "hit_x",
        "hit_y",
        "hit_z",
        "block_state_before",
        "block_state_after",
        "neighbors_json"
    );
    private static final String STATE_SAMPLES_CSV_HEADER = String.join(
        ",",
        "schema_version",
        "session_id",
        "event_id",
        "sample_time_ns",
        "event_time_ns",
        "relative_ms",
        "yaw",
        "pitch",
        "player_x",
        "player_y",
        "player_z",
        "fov",
        "gui_scale",
        "fps_estimate",
        "sensitivity"
    );
    private static final String MOUSE_TRAJECTORY_CSV_HEADER = String.join(
        ",",
        "schema_version",
        "session_id",
        "event_id",
        "sample_time_ns",
        "event_time_ns",
        "relative_ms",
        "mouse_dx",
        "mouse_dy"
    );

    private final Path outputDirectory;
    private final SampleRingBuffer samples = new SampleRingBuffer(STATE_RING_BUFFER_CAPACITY);
    private final MouseDeltaRingBuffer mouseDeltas = new MouseDeltaRingBuffer(MOUSE_RING_BUFFER_CAPACITY);
    private RecordingSession activeSession;

    public DaqRecorder(Path gameDirectory) {
        this.outputDirectory = Objects.requireNonNull(gameDirectory).resolve("minecraft-daq");
    }

    public synchronized RecordingSession start() throws IOException {
        if (activeSession != null) {
            return activeSession;
        }

        Files.createDirectories(outputDirectory);
        String sessionId = newSessionId();
        Instant startedAt = Instant.now();
        Path outputPath = outputDirectory.resolve(
            "mining-" + FILE_TIME_FORMAT.format(startedAt) + "-" + sessionId.substring(0, 12)
        );
        Files.createDirectories(outputPath);
        BufferedWriter eventsWriter = newCsvWriter(outputPath.resolve("events.csv"), EVENTS_CSV_HEADER);
        BufferedWriter stateSamplesWriter = newCsvWriter(
            outputPath.resolve("state_samples.csv"),
            STATE_SAMPLES_CSV_HEADER
        );
        BufferedWriter mouseTrajectoryWriter = newCsvWriter(
            outputPath.resolve("mouse_trajectory.csv"),
            MOUSE_TRAJECTORY_CSV_HEADER
        );

        samples.clear();
        mouseDeltas.clear();
        activeSession = new RecordingSession(
            sessionId,
            startedAt,
            System.nanoTime(),
            outputPath,
            eventsWriter,
            stateSamplesWriter,
            mouseTrajectoryWriter
        );
        return activeSession;
    }

    public synchronized RecordingSummary stop() throws IOException {
        if (activeSession == null) {
            return null;
        }

        RecordingSession session = activeSession;
        activeSession = null;
        session.flush();
        session.close();
        return new RecordingSummary(
            session.sessionId(),
            session.outputPath(),
            session.startedAt(),
            Instant.now(),
            session.eventCount(),
            session.stateSampleCount(),
            session.mouseDeltaCount(),
            samples.size(),
            samples.capacity(),
            mouseDeltas.size(),
            mouseDeltas.capacity()
        );
    }

    public synchronized RecordingSession activeSession() {
        return activeSession;
    }

    public synchronized boolean isRecording() {
        return activeSession != null;
    }

    public synchronized void recordSample(RecordingSample sample) {
        if (activeSession == null) {
            return;
        }
        samples.add(sample);
        activeSession.recordStateSample();
    }

    public synchronized void recordMouseDelta(double dx, double dy) {
        if (activeSession == null) {
            return;
        }
        mouseDeltas.add(new MouseDeltaSample(System.nanoTime(), dx, dy));
        activeSession.recordMouseDelta();
    }

    public synchronized void recordMiningEvent(MiningEventData event) throws IOException {
        if (activeSession == null) {
            return;
        }

        RecordingSession session = activeSession;
        long eventId = session.nextEventId();
        writeEvent(session, eventId, event);
        List<RecordingSample> eventSamples = samples.recentSince(event.eventTimeNs() - EVENT_WINDOW_NS);
        for (RecordingSample sample : eventSamples) {
            writeStateSample(session, eventId, event, sample);
        }
        List<MouseDeltaSample> eventMouseDeltas = mouseDeltas.recentSince(event.eventTimeNs() - EVENT_WINDOW_NS);
        for (MouseDeltaSample sample : eventMouseDeltas) {
            writeMouseDelta(session, eventId, event, sample);
        }
        session.flush();
    }

    public Path outputDirectory() {
        return outputDirectory;
    }

    private static String newSessionId() {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(Long.toString(System.currentTimeMillis()).getBytes(StandardCharsets.UTF_8));
            digest.update((byte) ':');
            digest.update(Long.toString(System.nanoTime()).getBytes(StandardCharsets.UTF_8));
            digest.update((byte) ':');
            digest.update(UUID.randomUUID().toString().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is not available", exception);
        }
    }

    private static BufferedWriter newCsvWriter(Path outputPath, String header) throws IOException {
        BufferedWriter writer = Files.newBufferedWriter(outputPath, StandardCharsets.UTF_8);
        writer.write(header);
        writer.newLine();
        writer.flush();
        return writer;
    }

    private static void writeEvent(
        RecordingSession session,
        long eventId,
        MiningEventData event
    ) throws IOException {
        StringBuilder row = new StringBuilder(512);
        appendCsv(row, Integer.toString(SCHEMA_VERSION));
        appendCsv(row, session.sessionId());
        appendCsv(row, Long.toString(eventId));
        appendCsv(row, Long.toString(event.eventTimeNs()));
        appendCsv(row, Integer.toString(event.targetX()));
        appendCsv(row, Integer.toString(event.targetY()));
        appendCsv(row, Integer.toString(event.targetZ()));
        appendCsv(row, event.faceId());
        appendCsv(row, doubleOrEmpty(event.hitX()));
        appendCsv(row, doubleOrEmpty(event.hitY()));
        appendCsv(row, doubleOrEmpty(event.hitZ()));
        appendCsv(row, event.blockStateBefore());
        appendCsv(row, event.blockStateAfter());
        appendCsv(row, event.neighborsJson());
        session.eventsWriter().write(row.toString());
        session.eventsWriter().newLine();
    }

    private static void writeStateSample(
        RecordingSession session,
        long eventId,
        MiningEventData event,
        RecordingSample sample
    ) throws IOException {
        StringBuilder row = new StringBuilder(512);
        appendCsv(row, Integer.toString(SCHEMA_VERSION));
        appendCsv(row, session.sessionId());
        appendCsv(row, Long.toString(eventId));
        appendCsv(row, Long.toString(sample.sampleTimeNs()));
        appendCsv(row, Long.toString(event.eventTimeNs()));
        appendCsv(row, Double.toString((sample.sampleTimeNs() - event.eventTimeNs()) / 1_000_000.0));
        appendCsv(row, Float.toString(sample.yaw()));
        appendCsv(row, Float.toString(sample.pitch()));
        appendCsv(row, Double.toString(sample.playerX()));
        appendCsv(row, Double.toString(sample.playerY()));
        appendCsv(row, Double.toString(sample.playerZ()));
        appendCsv(row, Integer.toString(sample.fov()));
        appendCsv(row, Integer.toString(sample.guiScale()));
        appendCsv(row, Integer.toString(sample.fpsEstimate()));
        appendCsv(row, Double.toString(sample.sensitivity()));
        session.stateSamplesWriter().write(row.toString());
        session.stateSamplesWriter().newLine();
    }

    private static void writeMouseDelta(
        RecordingSession session,
        long eventId,
        MiningEventData event,
        MouseDeltaSample sample
    ) throws IOException {
        StringBuilder row = new StringBuilder(256);
        appendCsv(row, Integer.toString(SCHEMA_VERSION));
        appendCsv(row, session.sessionId());
        appendCsv(row, Long.toString(eventId));
        appendCsv(row, Long.toString(sample.sampleTimeNs()));
        appendCsv(row, Long.toString(event.eventTimeNs()));
        appendCsv(row, Double.toString((sample.sampleTimeNs() - event.eventTimeNs()) / 1_000_000.0));
        appendCsv(row, Double.toString(sample.mouseDx()));
        appendCsv(row, Double.toString(sample.mouseDy()));
        session.mouseTrajectoryWriter().write(row.toString());
        session.mouseTrajectoryWriter().newLine();
    }

    private static String doubleOrEmpty(double value) {
        if (Double.isNaN(value)) {
            return "";
        }
        return Double.toString(value);
    }

    private static void appendCsv(StringBuilder out, String value) {
        if (!out.isEmpty()) {
            out.append(',');
        }

        boolean quote = value.isEmpty();
        for (int index = 0; index < value.length() && !quote; index++) {
            char ch = value.charAt(index);
            quote = ch == ',' || ch == '"' || ch == '\n' || ch == '\r';
        }

        if (!quote) {
            out.append(value);
            return;
        }

        out.append('"');
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            if (ch == '"') {
                out.append('"');
            }
            out.append(ch);
        }
        out.append('"');
    }

    public static final class RecordingSession {
        private final String sessionId;
        private final Instant startedAt;
        private final long startedAtNs;
        private final Path outputPath;
        private final BufferedWriter eventsWriter;
        private final BufferedWriter stateSamplesWriter;
        private final BufferedWriter mouseTrajectoryWriter;
        private long eventCount;
        private long stateSampleCount;
        private long mouseDeltaCount;

        private RecordingSession(
            String sessionId,
            Instant startedAt,
            long startedAtNs,
            Path outputPath,
            BufferedWriter eventsWriter,
            BufferedWriter stateSamplesWriter,
            BufferedWriter mouseTrajectoryWriter
        ) {
            this.sessionId = sessionId;
            this.startedAt = startedAt;
            this.startedAtNs = startedAtNs;
            this.outputPath = outputPath;
            this.eventsWriter = eventsWriter;
            this.stateSamplesWriter = stateSamplesWriter;
            this.mouseTrajectoryWriter = mouseTrajectoryWriter;
        }

        private void recordStateSample() {
            stateSampleCount++;
        }

        private void recordMouseDelta() {
            mouseDeltaCount++;
        }

        private long nextEventId() {
            eventCount++;
            return eventCount;
        }

        public String sessionId() {
            return sessionId;
        }

        public Instant startedAt() {
            return startedAt;
        }

        public long startedAtNs() {
            return startedAtNs;
        }

        public Path outputPath() {
            return outputPath;
        }

        public BufferedWriter eventsWriter() {
            return eventsWriter;
        }

        public BufferedWriter stateSamplesWriter() {
            return stateSamplesWriter;
        }

        public BufferedWriter mouseTrajectoryWriter() {
            return mouseTrajectoryWriter;
        }

        private void flush() throws IOException {
            eventsWriter.flush();
            stateSamplesWriter.flush();
            mouseTrajectoryWriter.flush();
        }

        private void close() throws IOException {
            try (eventsWriter; stateSamplesWriter; mouseTrajectoryWriter) {
                flush();
            }
        }

        public long eventCount() {
            return eventCount;
        }

        public long stateSampleCount() {
            return stateSampleCount;
        }

        public long mouseDeltaCount() {
            return mouseDeltaCount;
        }
    }

    public record RecordingSummary(
        String sessionId,
        Path outputPath,
        Instant startedAt,
        Instant stoppedAt,
        long eventCount,
        long stateSampleCount,
        long mouseDeltaCount,
        int bufferedStateSampleCount,
        int stateBufferCapacity,
        int bufferedMouseDeltaCount,
        int mouseBufferCapacity
    ) {
    }
}
