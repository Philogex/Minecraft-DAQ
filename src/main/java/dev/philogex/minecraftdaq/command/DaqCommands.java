package dev.philogex.minecraftdaq.command;

import dev.philogex.minecraftdaq.MinecraftDaqClient;
import dev.philogex.minecraftdaq.recording.DaqRecorder;
import dev.philogex.minecraftdaq.recording.DaqRecorder.RecordingSession;
import dev.philogex.minecraftdaq.recording.DaqRecorder.RecordingSummary;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource;
import net.minecraft.network.chat.Component;

import java.io.IOException;

import static net.fabricmc.fabric.api.client.command.v2.ClientCommands.literal;

public final class DaqCommands {
    private DaqCommands() {
    }

    public static void register(DaqRecorder recorder) {
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) -> dispatcher.register(
            literal("daq")
                .then(literal("start").executes(context -> start(recorder, context.getSource())))
                .then(literal("stop").executes(context -> stop(recorder, context.getSource())))
                .then(literal("status").executes(context -> status(recorder, context.getSource())))
        ));
    }

    private static int start(DaqRecorder recorder, FabricClientCommandSource source) {
        RecordingSession previousSession = recorder.activeSession();
        if (previousSession != null) {
            source.sendFeedback(Component.literal(
                "Minecraft DAQ is already recording: " + previousSession.outputPath()
            ));
            return 0;
        }

        try {
            RecordingSession session = recorder.start();
            source.sendFeedback(Component.literal(
                "Minecraft DAQ recording started: " + session.outputPath()
            ));
            MinecraftDaqClient.LOGGER.info(
                "Recording started: session={} path={}",
                session.sessionId(),
                session.outputPath()
            );
            return 1;
        } catch (IOException exception) {
            MinecraftDaqClient.LOGGER.error("Failed to start recording", exception);
            source.sendError(Component.literal(
                "Minecraft DAQ failed to start: " + exception.getMessage()
            ));
            return 0;
        }
    }

    private static int stop(DaqRecorder recorder, FabricClientCommandSource source) {
        try {
            RecordingSummary summary = recorder.stop();
            if (summary == null) {
                source.sendFeedback(Component.literal("Minecraft DAQ is not recording"));
                return 0;
            }

            source.sendFeedback(Component.literal(
                "Minecraft DAQ recording stopped: " + summary.outputPath()
            ));
            MinecraftDaqClient.LOGGER.info(
                "Recording stopped: session={} path={} events={} samples={} ticks={} frames={} buffered={}/{}",
                summary.sessionId(),
                summary.outputPath(),
                summary.eventCount(),
                summary.sampleCount(),
                summary.tickSampleCount(),
                summary.frameSampleCount(),
                summary.bufferedSampleCount(),
                summary.bufferCapacity()
            );
            return 1;
        } catch (IOException exception) {
            MinecraftDaqClient.LOGGER.error("Failed to stop recording", exception);
            source.sendError(Component.literal(
                "Minecraft DAQ failed to stop: " + exception.getMessage()
            ));
            return 0;
        }
    }

    private static int status(DaqRecorder recorder, FabricClientCommandSource source) {
        RecordingSession session = recorder.activeSession();
        if (session == null) {
            source.sendFeedback(Component.literal(
                "Minecraft DAQ is idle. Output directory: " + recorder.outputDirectory()
            ));
            return 0;
        }

        source.sendFeedback(Component.literal(
            "Minecraft DAQ recording: " + session.outputPath()
                + " samples=" + session.sampleCount()
                + " ticks=" + session.tickSampleCount()
                + " frames=" + session.frameSampleCount()
        ));
        return 1;
    }
}
