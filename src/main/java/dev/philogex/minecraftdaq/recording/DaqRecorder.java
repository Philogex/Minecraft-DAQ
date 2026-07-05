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
import java.util.Objects;
import java.util.UUID;

public final class DaqRecorder {
    private static final int SCHEMA_VERSION = 1;
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
            session.sampleCount()
        );
    }

    public synchronized RecordingSession activeSession() {
        return activeSession;
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

    public record RecordingSession(
        String sessionId,
        Instant startedAt,
        long startedAtNs,
        Path outputPath,
        BufferedWriter writer,
        long eventCount,
        long sampleCount
    ) {
        private RecordingSession(
            String sessionId,
            Instant startedAt,
            long startedAtNs,
            Path outputPath,
            BufferedWriter writer
        ) {
            this(sessionId, startedAt, startedAtNs, outputPath, writer, 0L, 0L);
        }
    }

    public record RecordingSummary(
        String sessionId,
        Path outputPath,
        Instant startedAt,
        Instant stoppedAt,
        long eventCount,
        long sampleCount
    ) {
    }
}
