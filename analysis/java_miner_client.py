"""Persistent, headless client for the Miner JAR's Java/JNI analysis interface.

No native Python module or Minecraft process is needed. Configuration and block
encoding are owned by Java; this module only transports requests and results.
"""

from __future__ import annotations

import atexit
import io
import os
import platform
import secrets
import struct
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from analysis.aim_features import AimPoint


MINER_ROOT = Path(os.environ.get(
    "MINESCRIPT_MINER_ROOT", Path(__file__).resolve().parents[2] / "Minescript-Miner"
))
GENERATORS = ("minimum_jerk", "sigmadrift", "geometry_feedback_sigmadrift")
Orientation = tuple[float, float]
_MAX_FRAME = 16 * 1024 * 1024


@dataclass(frozen=True)
class TargetMetrics:
    yaw: float
    pitch: float
    width_yaw: float
    width_pitch: float
    distance: float
    effective_width: float = 0.0
    target_block: tuple[int, int, int] | None = None
    face_id: str | None = None
    hit_point: tuple[float, float, float] | None = None
    visible_components: tuple[tuple[tuple[float, float, float], ...], ...] = ()


@dataclass(frozen=True)
class AimGeneration:
    points: tuple[AimPoint, ...]
    diagnostics: dict[str, object]


def _encode(value: object) -> bytes:
    if value is None:
        return b"\x00"
    if isinstance(value, bool):
        return b"\x01" + bytes([value])
    if isinstance(value, float):
        return b"\x02" + struct.pack(">d", value)
    if isinstance(value, int):
        return b"\x03" + struct.pack(">q", value)
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"\x04" + struct.pack(">i", len(encoded)) + encoded
    if isinstance(value, (list, tuple)):
        return b"\x05" + struct.pack(">i", len(value)) + b"".join(map(_encode, value))
    if isinstance(value, dict):
        return b"\x06" + struct.pack(">i", len(value)) + b"".join(
            _encode(key) + _encode(item) for key, item in value.items()
        )
    raise TypeError(f"unsupported analysis value: {type(value).__name__}")


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        data = stream.read(size - len(chunks))
        if not data:
            raise RuntimeError("Miner Java interface closed before returning a complete response")
        chunks.extend(data)
    return bytes(chunks)


def _decode(stream: io.BytesIO, depth: int = 0):
    if depth > 32:
        raise RuntimeError("analysis response nesting too deep")
    tag = _read_exact(stream, 1)[0]
    if tag == 0:
        return None
    if tag == 1:
        return bool(_read_exact(stream, 1)[0])
    if tag == 2:
        return struct.unpack(">d", _read_exact(stream, 8))[0]
    if tag == 3:
        return struct.unpack(">q", _read_exact(stream, 8))[0]
    if tag not in (4, 5, 6):
        raise RuntimeError(f"unknown analysis response tag: {tag}")
    size = struct.unpack(">i", _read_exact(stream, 4))[0]
    if size < 0 or size > len(stream.getbuffer()) - stream.tell():
        raise RuntimeError("invalid analysis response value length")
    if tag == 4:
        return _read_exact(stream, size).decode("utf-8")
    if tag == 5:
        return [_decode(stream, depth + 1) for _ in range(size)]
    result = {}
    for _ in range(size):
        key = _decode(stream, depth + 1)
        if not isinstance(key, str):
            raise RuntimeError("expected string response key")
        result[key] = _decode(stream, depth + 1)
    return result


def _jar_path(explicit: Path | None) -> Path:
    if explicit is not None or os.environ.get("MINECRAFT_MINER_JAR"):
        path = Path(explicit or os.environ["MINECRAFT_MINER_JAR"]).resolve()
        if not path.is_file():
            raise RuntimeError(f"Miner JAR does not exist: {path}")
        return path
    os_name = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(platform.system())
    arch = {"amd64": "x86_64", "x86_64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
    jars = list((MINER_ROOT / "fabric/build/libs").glob(f"minecraft-miner-*-{os_name}-{arch}.jar"))
    if not jars:
        raise RuntimeError(
            "Miner JAR missing. Run './gradlew build' in Minescript-Miner/fabric "
            "or set MINECRAFT_MINER_JAR to the matching platform JAR."
        )
    return max(jars, key=lambda path: path.stat().st_mtime).resolve()


class JavaMinerClient:
    def __init__(self, model: str = "minimum_jerk", config_path: Path | None = None,
                 *, jar_path: Path | None = None, java: str | None = None):
        if model not in GENERATORS:
            raise ValueError(f"unsupported Miner generator: {model}")
        self.jar_path = _jar_path(jar_path)
        java_home = os.environ.get("JAVA_HOME")
        executable = java or os.environ.get("MINECRAFT_MINER_JAVA") or (
            str(Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")) if java_home else "java"
        )
        self._lock = threading.Lock()
        self._process = subprocess.Popen(
            [executable, "--enable-native-access=ALL-UNNAMED", "-cp", str(self.jar_path),
             "dev.philogex.miner.bridge.AnalysisServer"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            # Inherit stderr: Java errors stay visible and cannot fill a separate unread pipe.
        )
        try:
            metadata = self.request(op="hello", protocol=1, model=model,
                                    config=str(config_path.resolve()) if config_path is not None else None)
            self.config_metadata = metadata["config"]
            self.backend_metadata = metadata["backend"]
            self.max_cube_side = metadata["max_cube_side"]
            self.full_cube_shape_id = metadata["full_cube_shape_id"]
            self.model = model
            # A view for plotting code; parsing and validation have already happened in Java.
            self.config = SimpleNamespace(**{
                key: SimpleNamespace(**value) if isinstance(value, dict) else value
                for key, value in self.config_metadata.items()
            })
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)

    def request(self, **request):
        payload = _encode(request)
        if len(payload) > _MAX_FRAME:
            raise ValueError("analysis request exceeds 16 MiB")
        with self._lock:
            if self._process.poll() is not None or self._process.stdin.closed:
                raise RuntimeError("Miner Java interface is closed")
            self._process.stdin.write(struct.pack(">i", len(payload)) + payload)
            self._process.stdin.flush()
            size = struct.unpack(">i", _read_exact(self._process.stdout, 4))[0]
            if size < 1 or size > _MAX_FRAME:
                raise RuntimeError("invalid analysis response frame length")
            frame = io.BytesIO(_read_exact(self._process.stdout, size))
            response = _decode(frame)
            if frame.tell() != size:
                raise RuntimeError("trailing analysis response data")
            if "error" in response:
                raise ValueError(response["error"])
            return response["result"]

    def acquire(self, eye, orientation, side, reach, states, targets) -> TargetMetrics | None:
        result = self.request(op="scan", pose=(*eye, *orientation), side=side,
                              reach=reach, states=states, targets=targets)
        if result is None:
            return None
        metrics = result["metrics"]
        return TargetMetrics(*metrics,
            target_block=tuple(result["target_block"]), face_id=result["face_id"],
            hit_point=tuple(result["hit_point"]), visible_components=tuple(
                tuple(tuple(direction) for direction in component) for component in result["visible_components"]
            ))

    def generate(self, start: Orientation, target: TargetMetrics, step: float,
                 *, seed: int | None = None, model: str | None = None) -> AimGeneration:
        if seed is None:
            seed = secrets.randbits(64)
        if not isinstance(seed, int) or not 0 <= seed < 2**64:
            raise ValueError("seed must fit in an unsigned 64-bit integer")
        result = self.request(op="aim", start=start,
            metrics=(target.yaw, target.pitch, target.width_yaw, target.width_pitch,
                     target.distance, target.effective_width),
            components=target.visible_components, step=step, seed=str(seed), model=model or self.model)
        return AimGeneration(tuple(AimPoint(*point) for point in result["points"]), result["diagnostics"])

    def shape_id(self, state: str) -> int:
        return self.request(op="shape_ids", states=[state])[0]

    def angular_step_deg(self, sensitivity: float) -> float:
        return self.request(op="angular_step", sensitivity=sensitivity)

    def close(self):
        with self._lock:
            if not self._process.stdin.closed:
                self._process.stdin.close()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process.stdout.close()
        atexit.unregister(self.close)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
