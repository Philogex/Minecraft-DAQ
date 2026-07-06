package dev.philogex.minecraftdaq.mining;

import dev.philogex.minecraftdaq.MinecraftDaqClient;
import dev.philogex.minecraftdaq.recording.DaqRecorder;
import dev.philogex.minecraftdaq.recording.MiningEventData;
import java.io.IOException;
import net.fabricmc.fabric.api.event.client.player.ClientPlayerBlockBreakEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

public final class DaqMiningEvents {
    private DaqMiningEvents() {
    }

    public static void register(DaqRecorder recorder) {
        ClientPlayerBlockBreakEvents.AFTER.register((level, player, pos, state) ->
            recordBreak(recorder, level, player, pos, state)
        );
    }

    private static void recordBreak(
        DaqRecorder recorder,
        ClientLevel level,
        LocalPlayer player,
        BlockPos pos,
        BlockState stateBefore
    ) {
        if (!recorder.isRecording()) {
            return;
        }

        long eventTimeNs = System.nanoTime();
        BlockState stateAfter = level.getBlockState(pos);
        HitInfo hitInfo = currentHitInfo(pos);
        MiningEventData event = new MiningEventData(
            eventTimeNs,
            pos.getX(),
            pos.getY(),
            pos.getZ(),
            hitInfo.faceId(),
            hitInfo.hitX(),
            hitInfo.hitY(),
            hitInfo.hitZ(),
            blockStateId(stateBefore),
            blockStateId(stateAfter),
            neighborsJson(level, pos)
        );

        try {
            recorder.recordMiningEvent(event);
        } catch (IOException exception) {
            MinecraftDaqClient.LOGGER.error("Failed to write Minecraft DAQ mining event", exception);
        }
    }

    private static HitInfo currentHitInfo(BlockPos target) {
        HitResult hitResult = Minecraft.getInstance().hitResult;
        if (!(hitResult instanceof BlockHitResult blockHitResult)) {
            return HitInfo.empty();
        }
        if (blockHitResult.getType() != HitResult.Type.BLOCK) {
            return HitInfo.empty();
        }
        if (!blockHitResult.getBlockPos().equals(target)) {
            return HitInfo.empty();
        }

        Vec3 location = blockHitResult.getLocation();
        return new HitInfo(
            blockHitResult.getDirection().getName(),
            location.x(),
            location.y(),
            location.z()
        );
    }

    private static String neighborsJson(ClientLevel level, BlockPos center) {
        StringBuilder out = new StringBuilder(2048);
        out.append('[');
        boolean first = true;
        for (int dx = -1; dx <= 1; dx++) {
            for (int dy = -1; dy <= 1; dy++) {
                for (int dz = -1; dz <= 1; dz++) {
                    if (dx == 0 && dy == 0 && dz == 0) {
                        continue;
                    }
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    BlockState state = level.getBlockState(center.offset(dx, dy, dz));
                    out.append("{\"dx\":").append(dx)
                        .append(",\"dy\":").append(dy)
                        .append(",\"dz\":").append(dz)
                        .append(",\"state\":\"");
                    appendJsonStringContent(out, blockStateId(state));
                    out.append("\"}");
                }
            }
        }
        out.append(']');
        return out.toString();
    }

    private static String blockStateId(BlockState state) {
        StringBuilder out = new StringBuilder();
        out.append(BuiltInRegistries.BLOCK.getKey(state.getBlock()));
        if (!state.getProperties().isEmpty()) {
            out.append('[');
            boolean first = true;
            for (Property<?> property : state.getProperties()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                out.append(property.getName()).append('=');
                appendPropertyValue(out, state, property);
            }
            out.append(']');
        }
        return out.toString();
    }

    private static <T extends Comparable<T>> void appendPropertyValue(
        StringBuilder out,
        BlockState state,
        Property<T> property
    ) {
        out.append(property.getName(state.getValue(property)));
    }

    private static void appendJsonStringContent(StringBuilder out, String value) {
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            if (ch == '"' || ch == '\\') {
                out.append('\\');
            }
            out.append(ch);
        }
    }

    private record HitInfo(String faceId, double hitX, double hitY, double hitZ) {
        private static HitInfo empty() {
            return new HitInfo("", Double.NaN, Double.NaN, Double.NaN);
        }
    }
}
