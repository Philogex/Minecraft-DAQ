package dev.philogex.minecraftdaq.mixin;

import dev.philogex.minecraftdaq.sampling.MouseDeltaCapture;
import net.minecraft.client.MouseHandler;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MouseHandler.class)
public abstract class MouseHandlerMixin {
    @Shadow
    private double accumulatedDX;

    @Shadow
    private double accumulatedDY;

    @Inject(method = "handleAccumulatedMovement", at = @At("HEAD"))
    private void minecraftDaq$recordAccumulatedMovement(CallbackInfo info) {
        if (accumulatedDX != 0.0 || accumulatedDY != 0.0) {
            MouseDeltaCapture.record(accumulatedDX, accumulatedDY);
        }
    }
}
