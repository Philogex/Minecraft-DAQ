package dev.philogex.minecraftdaq.sampling;

import dev.philogex.minecraftdaq.recording.DaqRecorder;
import dev.philogex.minecraftdaq.recording.RecordingSample;
import dev.philogex.minecraftdaq.recording.SampleSource;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.rendering.v1.level.LevelRenderEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;

public final class DaqSampler {
    private DaqSampler() {
    }

    public static void register(DaqRecorder recorder) {
        ClientTickEvents.END_CLIENT_TICK.register(client -> sample(recorder, client, SampleSource.TICK));
        LevelRenderEvents.END_MAIN.register(context -> sample(recorder, Minecraft.getInstance(), SampleSource.FRAME));
    }

    private static void sample(DaqRecorder recorder, Minecraft client, SampleSource source) {
        if (!recorder.isRecording()) {
            return;
        }

        LocalPlayer player = client.player;
        if (player == null) {
            return;
        }

        int guiScale = 0;
        if (client.getWindow() != null) {
            guiScale = client.getWindow().getGuiScale();
        }

        recorder.recordSample(new RecordingSample(
            source,
            System.nanoTime(),
            player.getYRot(),
            player.getXRot(),
            player.getX(),
            player.getY(),
            player.getZ(),
            client.options.fov().get(),
            guiScale,
            client.getFps(),
            client.options.sensitivity().get()
        ));
    }
}
