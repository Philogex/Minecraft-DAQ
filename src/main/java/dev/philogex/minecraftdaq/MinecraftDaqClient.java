package dev.philogex.minecraftdaq;

import dev.philogex.minecraftdaq.command.DaqCommands;
import dev.philogex.minecraftdaq.mining.DaqMiningEvents;
import dev.philogex.minecraftdaq.recording.DaqRecorder;
import dev.philogex.minecraftdaq.sampling.DaqSampler;
import dev.philogex.minecraftdaq.sampling.MouseDeltaCapture;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class MinecraftDaqClient implements ClientModInitializer {
    public static final String MOD_ID = "minecraft_daq";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    @Override
    public void onInitializeClient() {
        DaqRecorder recorder = new DaqRecorder(FabricLoader.getInstance().getGameDir());
        DaqCommands.register(recorder);
        DaqSampler.register(recorder);
        MouseDeltaCapture.register(recorder);
        DaqMiningEvents.register(recorder);
        LOGGER.info("Minecraft DAQ initialized");
    }
}
