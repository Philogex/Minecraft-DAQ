package dev.philogex.minecraftdaq.hud;

import dev.philogex.minecraftdaq.MinecraftDaqClient;
import dev.philogex.minecraftdaq.recording.DaqRecorder;
import dev.philogex.minecraftdaq.recording.DaqRecorder.RecordingStats;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.hud.VanillaHudElements;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.resources.Identifier;

public final class DaqHudOverlay {
    private static final Identifier HUD_ID =
        Identifier.fromNamespaceAndPath(MinecraftDaqClient.MOD_ID, "recording_status");
    private static final int BACKGROUND = 0x90000000;
    private static final int ACCENT = 0xFFFF5555;
    private static final int TEXT = 0xFFE6E6E6;
    private static final int MUTED_TEXT = 0xFFB8B8B8;

    private DaqHudOverlay() {
    }

    public static void register(DaqRecorder recorder) {
        HudElementRegistry.attachElementAfter(
            VanillaHudElements.CHAT,
            HUD_ID,
            (graphics, tickCounter) -> render(graphics, recorder)
        );
    }

    private static void render(GuiGraphicsExtractor graphics, DaqRecorder recorder) {
        RecordingStats stats = recorder.recordingStats();
        if (stats == null) {
            return;
        }

        Minecraft client = Minecraft.getInstance();
        Font font = client.font;
        String title = "DAQ REC";
        String events = "events: " + stats.eventCount();
        String state = "state:  " + stats.stateSampleCount();
        String mouse = "mouse:  " + stats.mouseDeltaCount();
        String session = "session: " + stats.sessionId().substring(0, 8);

        int x = 8;
        int y = 8;
        int line = font.lineHeight + 2;
        int width = Math.max(
            Math.max(font.width(title), font.width(events)),
            Math.max(Math.max(font.width(state), font.width(mouse)), font.width(session))
        ) + 12;
        int height = line * 5 + 8;

        graphics.fill(x - 4, y - 4, x + width, y + height - 4, BACKGROUND);
        graphics.text(font, title, x, y, ACCENT, true);
        graphics.text(font, events, x, y + line, TEXT, true);
        graphics.text(font, state, x, y + line * 2, TEXT, true);
        graphics.text(font, mouse, x, y + line * 3, TEXT, true);
        graphics.text(font, session, x, y + line * 4, MUTED_TEXT, true);
    }
}
