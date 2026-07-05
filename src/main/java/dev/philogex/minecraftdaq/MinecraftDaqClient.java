package dev.philogex.minecraftdaq;

import net.fabricmc.api.ClientModInitializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class MinecraftDaqClient implements ClientModInitializer {
    public static final String MOD_ID = "minecraft_daq";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    @Override
    public void onInitializeClient() {
        LOGGER.info("Minecraft DAQ initialized");
    }
}
