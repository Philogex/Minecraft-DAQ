package dev.philogex.minecraftdaq.mining;

import dev.philogex.minecraftdaq.MinecraftDaqClient;
import dev.philogex.minecraftdaq.recording.DaqRecorder;
import dev.philogex.minecraftdaq.recording.DaqRecorder.RecordingSession;
import dev.philogex.minecraftdaq.recording.MiningEventData;
import dev.philogex.minecraftdaq.recording.MiningStartData;
import java.io.IOException;
import net.fabricmc.fabric.api.event.client.player.ClientPlayerBlockBreakEvents;
import net.fabricmc.fabric.api.event.player.AttackBlockCallback;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

public final class DaqMiningEvents {
    private static final int MAX_CUBE_SIDE = 39;

    private DaqMiningEvents() {
    }

    public static void register(DaqRecorder recorder) {
        MiningStartTracker starts = new MiningStartTracker(recorder);
        AttackBlockCallback.EVENT.register(starts::recordStart);
        ClientPlayerBlockBreakEvents.AFTER.register((level, player, pos, state) ->
            recordBreak(recorder, starts, level, player, pos, state)
        );
    }

    private static void recordBreak(
        DaqRecorder recorder,
        MiningStartTracker starts,
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
        MiningStartData start = starts.take(pos);
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
            start == null ? neighborsJson(level, pos) : start.neighborsJson(),
            start
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

    private static String worldSnapshotJson(
        ClientLevel level,
        LocalPlayer player,
        BlockPos target
    ) {
        BlockPos center = BlockPos.containing(player.getEyePosition());
        int half = Math.max(
            Math.max(
                Math.abs(target.getX() - center.getX()),
                Math.abs(target.getY() - center.getY())
            ),
            Math.abs(target.getZ() - center.getZ())
        );
        int side = half * 2 + 1;
        if (side > MAX_CUBE_SIDE) {
            return "";
        }

        int originX = center.getX() - half;
        int originY = center.getY() - half;
        int originZ = center.getZ() - half;
        int blockCount = side * side * side;
        StringBuilder out = new StringBuilder(96 + blockCount * 24);
        out.append("{\"origin\":[")
            .append(originX).append(',')
            .append(originY).append(',')
            .append(originZ)
            .append("],\"side\":").append(side)
            .append(",\"blocks\":[");

        boolean first = true;
        for (int y = 0; y < side; y++) {
            for (int z = 0; z < side; z++) {
                for (int x = 0; x < side; x++) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    out.append('"');
                    appendJsonStringContent(
                        out,
                        blockStateId(level.getBlockState(
                            new BlockPos(originX + x, originY + y, originZ + z)
                        ))
                    );
                    out.append('"');
                }
            }
        }
        out.append("]}");
        return out.toString();
    }

    private static Integer expectedBreakTicks(float destroyProgressPerTick) {
        if (!(destroyProgressPerTick > 0.0F) || !Float.isFinite(destroyProgressPerTick)) {
            return null;
        }
        return Math.max(1, (int) Math.ceil(1.0 / destroyProgressPerTick));
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

    private static final class MiningStartTracker {
        private final DaqRecorder recorder;
        private PendingStart latest;

        private MiningStartTracker(DaqRecorder recorder) {
            this.recorder = recorder;
        }

        private InteractionResult recordStart(
            Player player,
            Level level,
            InteractionHand hand,
            BlockPos pos,
            Direction direction
        ) {
            RecordingSession session = recorder.activeSession();
            if (session == null ||
                !(player instanceof LocalPlayer localPlayer) ||
                !(level instanceof ClientLevel clientLevel)) {
                // The same Fabric event also fires on the integrated server.
                // Ignore that invocation without discarding the client start.
                return InteractionResult.PASS;
            }

            BlockState state = clientLevel.getBlockState(pos);
            float progress = state.getDestroyProgress(localPlayer, clientLevel, pos);
            MiningStartData data = new MiningStartData(
                System.nanoTime(),
                progress,
                expectedBreakTicks(progress),
                neighborsJson(clientLevel, pos),
                worldSnapshotJson(clientLevel, localPlayer, pos)
            );
            latest = new PendingStart(session.sessionId(), pos.immutable(), data);
            return InteractionResult.PASS;
        }

        private MiningStartData take(BlockPos pos) {
            RecordingSession session = recorder.activeSession();
            PendingStart pending = latest;
            if (session == null || pending == null ||
                !pending.sessionId().equals(session.sessionId()) ||
                !pending.target().equals(pos)) {
                return null;
            }
            latest = null;
            return pending.data();
        }
    }

    private record PendingStart(String sessionId, BlockPos target, MiningStartData data) {
    }
}
