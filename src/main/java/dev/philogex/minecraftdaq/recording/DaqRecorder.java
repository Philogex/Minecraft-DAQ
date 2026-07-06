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
    private static final int RING_BUFFER_CAPACITY = 8192;
    private static final long EVENT_WINDOW_NS = 1_500_000_000L;
    private static final DateTimeFormatter FILE_TIME_FORMAT =
        DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").withZone(ZoneOffset.UTC);
    private static final String CSV_HEADER = String.join(
        ",",
        "schema_version",
        "session_id",
        "event_id",
        "sample_time_ns",
        "event_time_ns",
        "relative_ms",
        "mouse_dx",
        "mouse_dy",
        "yaw",
        "pitch",
        "player_x",
        "player_y",
        "player_z",
        "target_x",
        "target_y",
        "target_z",
        "face_id",
        "hit_x",
        "hit_y",
        "hit_z",
        "block_state_before",
        "block_state_after",
        "neighbors_json",
        "fov",
        "gui_scale",
        "fps_estimate",
        "sensitivity"
    );

    private final Path outputDirectory;
    private final SampleRingBuffer samples = new SampleRingBuffer(RING_BUFFER_CAPACITY);
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
            "mining-" + FILE_TIME_FORMAT.format(startedAt) + "-" + sessionId.substring(0, 12) + ".csv"
        );
        BufferedWriter writer = Files.newBufferedWriter(
            outputPath,
            StandardCharsets.UTF_8
        );
        writer.write(CSV_HEADER);
        writer.newLine();
        writer.flush();

        samples.clear();
        activeSession = new RecordingSession(
            sessionId,
            startedAt,
            System.nanoTime(),
            outputPath,
            writer
        );
        return activeSession;
    }

    public synchronized RecordingSummary stop() throws IOException {
        if (activeSession == null) {
            return null;
        }

        RecordingSession session = activeSession;
        activeSession = null;
        session.writer().flush();
        session.writer().close();
        return new RecordingSummary(
            session.sessionId(),
            session.outputPath(),
            session.startedAt(),
            Instant.now(),
            session.eventCount(),
            session.sampleCount(),
            session.tickSampleCount(),
            session.frameSampleCount(),
            samples.size(),
            samples.capacity()
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
        activeSession.recordSample(sample.source());
    }

    public synchronized void recordMiningEvent(MiningEventData event) throws IOException {
        if (activeSession == null) {
            return;
        }

        RecordingSession session = activeSession;
        long eventId = session.nextEventId();
        List<RecordingSample> eventSamples = samples.recentSince(event.eventTimeNs() - EVENT_WINDOW_NS);
        for (RecordingSample sample : eventSamples) {
            writeMiningSample(session, eventId, event, sample);
        }
        session.writer().flush();
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

    private static void writeMiningSample(
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
        appendCsv(row, "");
        appendCsv(row, "");
        appendCsv(row, Float.toString(sample.yaw()));
        appendCsv(row, Float.toString(sample.pitch()));
        appendCsv(row, Double.toString(sample.playerX()));
        appendCsv(row, Double.toString(sample.playerY()));
        appendCsv(row, Double.toString(sample.playerZ()));
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
        appendCsv(row, Integer.toString(sample.fov()));
        appendCsv(row, Integer.toString(sample.guiScale()));
        appendCsv(row, Integer.toString(sample.fpsEstimate()));
        appendCsv(row, Double.toString(sample.sensitivity()));
        session.writer().write(row.toString());
        session.writer().newLine();
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
        private final BufferedWriter writer;
        private long eventCount;
        private long sampleCount;
        private long tickSampleCount;
        private long frameSampleCount;

        private RecordingSession(
            String sessionId,
            Instant startedAt,
            long startedAtNs,
            Path outputPath,
            BufferedWriter writer
        ) {
            this.sessionId = sessionId;
            this.startedAt = startedAt;
            this.startedAtNs = startedAtNs;
            this.outputPath = outputPath;
            this.writer = writer;
        }

        private void recordSample(SampleSource source) {
            sampleCount++;
            if (source == SampleSource.TICK) {
                tickSampleCount++;
            } else if (source == SampleSource.FRAME) {
                frameSampleCount++;
            }
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

        public BufferedWriter writer() {
            return writer;
        }

        public long eventCount() {
            return eventCount;
        }

        public long sampleCount() {
            return sampleCount;
        }

        public long tickSampleCount() {
            return tickSampleCount;
        }

        public long frameSampleCount() {
            return frameSampleCount;
        }
    }

    public record RecordingSummary(
        String sessionId,
        Path outputPath,
        Instant startedAt,
        Instant stoppedAt,
        long eventCount,
        long sampleCount,
        long tickSampleCount,
        long frameSampleCount,
        int bufferedSampleCount,
        int bufferCapacity
    ) {
    }
}
