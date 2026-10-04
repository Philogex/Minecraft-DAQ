"""Integration checks against the actual platform Miner JAR, without Minecraft."""

import hashlib
import json
import math
import platform
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from analysis.java_miner_backend import GenerationCaseError, JavaMinerBackend
from analysis.java_miner_client import JavaMinerClient, TargetMetrics
from analysis.mining_session import MiningEvent, RecordedMiningEvent, StateSample, WorldSnapshot, load_mining_session
from analysis.path_dataset import write_generated_dataset


REFERENCE = json.loads((Path(__file__).parent / "fixtures/python_backend_reference.json").read_text())


def recorded_reference():
    data = REFERENCE["recorded"]
    event = MiningEvent(**{**data["event"], "neighbors": tuple(data["event"]["neighbors"])})
    return RecordedMiningEvent(event, tuple(StateSample(**sample) for sample in data["state_samples"]), ())


class JavaMinerIntegrationTest(unittest.TestCase):
    def assert_nested_equal(self, expected, actual):
        if isinstance(expected, dict):
            self.assertEqual(set(expected), set(actual))
            for key in expected:
                self.assert_nested_equal(expected[key], actual[key])
        elif isinstance(expected, (list, tuple)):
            self.assertEqual(len(expected), len(actual))
            for before, after in zip(expected, actual):
                self.assert_nested_equal(before, after)
        elif isinstance(expected, float):
            if math.isnan(expected):
                self.assertTrue(math.isnan(actual))
            else:
                self.assertAlmostEqual(expected, actual, delta=1e-12)
        else:
            self.assertEqual(expected, actual)

    def test_recorded_python_results_survive_java_migration(self):
        recorded = recorded_reference()
        for model, reference in REFERENCE["models"].items():
            with self.subTest(model=model), JavaMinerBackend(model) as backend:
                case = backend.prepare_case(recorded.event.session_id, recorded, eye_height=1.62)
                self.assert_nested_equal(reference["case"], asdict(case))
                self.assert_nested_equal(reference["config"], backend.config_metadata)
                generated = backend.generate(case, replicate_index=0, replicate_count=1, seed=2**64 - 1)
                repeated = backend.generate(case, replicate_index=0, replicate_count=1, seed=2**64 - 1)
                self.assertEqual(generated.points, repeated.points)
                # Stochastic C++ distributions differ on MSVC; their behavioral checks
                # and native JUnit tests still run there.
                if platform.system() == "Linux" or model == "minimum_jerk":
                    self.assert_nested_equal(reference["points"], [asdict(point) for point in generated.points])
                    self.assert_nested_equal(reference["diagnostics"], dict(generated.diagnostics))
                    self.assert_nested_equal(reference["endpoint_hit"], dict(generated.endpoint_hit))

    def test_java_owns_catalog_encoding_and_scan_errors_do_not_stop_process(self):
        with JavaMinerClient() as client:
            self.assertEqual(0, client.shape_id("minecraft:air"))
            self.assertEqual(client.full_cube_shape_id, client.shape_id("minecraft:unknown_block"))
            self.assertEqual(client.shape_id("minecraft:oak_stairs[facing=east,half=top,shape=inner_left]"),
                client.shape_id("minecraft:oak_stairs[waterlogged=true,shape=inner_left,half=top,facing=east]"))
            self.assertIsNone(client.acquire((.5, .5, .5), (0., 0.), 3, 4.8, ["minecraft:air"] * 27, []))
            for side, states, targets in ((3, [], []), (41, [], []), (3, ["minecraft:air"] * 27, [27])):
                with self.assertRaises(ValueError):
                    client.acquire((.5, .5, .5), (0., 0.), side, 4.8, states, targets)
            self.assertAlmostEqual(.15, client.angular_step_deg(.5))

    def test_config_path_with_spaces_and_unicode_and_artifact_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "aim Prüfung.txt"
            config.write_text("fitts_a_ms: 37\nsample_hz: 60\n", encoding="utf-8")
            with JavaMinerClient("sigmadrift", config) as client:
                self.assertEqual(37., client.config_metadata["minimum_jerk"]["fitts_a_ms"])
                self.assertIsInstance(client.config_metadata["minimum_jerk"]["sample_hz"], int)
                self.assertEqual("sigmadrift", client.config_metadata["aim_model"])
                self.assertEqual(hashlib.sha256(client.jar_path.read_bytes()).hexdigest(), client.backend_metadata["jar_sha256"])
                self.assertEqual(1, client.backend_metadata["protocol"])
                self.assertEqual(64, len(client.backend_metadata["catalog_sha256"]))

    def test_feedback_requires_geometry_and_seed_is_uint64(self):
        with JavaMinerClient("geometry_feedback_sigmadrift") as client:
            target = TargetMetrics(0., 0., 2., 2., 4.)
            with self.assertRaisesRegex(ValueError, "visible components"):
                client.generate((10., -2.), target, .15, seed=12345)
            for seed in (-1, 2**64):
                with self.assertRaisesRegex(ValueError, "unsigned 64-bit"):
                    client.generate((10., -2.), target, .15, seed=seed)
            self.assertAlmostEqual(.15, client.angular_step_deg(.5))

    def test_world_snapshot_and_local_reconstruction_agree(self):
        recorded = recorded_reference()
        states = ["minecraft:air"] * 27
        states[16] = "minecraft:diamond_ore"
        snapshot = WorldSnapshot((-1, -1, -1), 3, tuple(states))
        with JavaMinerBackend("minimum_jerk") as backend:
            local = backend.prepare_case(recorded.event.session_id, recorded, eye_height=1.62)
            full = backend.prepare_case(recorded.event.session_id,
                replace(recorded, event=replace(recorded.event, world_snapshot=snapshot)), eye_height=1.62)
            self.assertEqual("daq_world_snapshot", full.target.width_source)
            self.assertEqual(local.visible_components, full.visible_components)
            self.assertEqual(local.effective_width, full.effective_width)

    def test_generated_dataset_round_trip_keeps_diagnostics_and_backend_provenance(self):
        recorded = recorded_reference()
        with JavaMinerBackend("geometry_feedback_sigmadrift") as backend, tempfile.TemporaryDirectory() as directory:
            case = backend.prepare_case(recorded.event.session_id, recorded, eye_height=1.62)
            generated = backend.generate(case, replicate_index=0, replicate_count=1, seed=2**64 - 1)
            output = Path(directory) / "generated"
            write_generated_dataset(output, [generated], source_session_path=Path(directory),
                generator_config=backend.config_metadata, backend_metadata=backend.backend_metadata, skipped_reasons={})
            loaded = load_mining_session(output)
            self.assertEqual(1, len(loaded.events))
            self.assertEqual(len(generated.points), len(loaded.events[0].state_samples))
            self.assertEqual("minecraft-miner", loaded.metadata["backend"]["package"])
            self.assertEqual(backend.backend_metadata["jar_sha256"], loaded.metadata["backend"]["jar_sha256"])
            self.assertTrue(generated.diagnostics["final_visible"])
            self.assertIsInstance(generated.diagnostics["feedback_check_count"], int)
            self.assertIsInstance(generated.diagnostics["final_visible"], bool)

    def test_invalid_recorded_context_preserves_skip_reasons(self):
        recorded = recorded_reference()
        with JavaMinerBackend("minimum_jerk") as backend:
            invalid = replace(recorded, state_samples=(replace(recorded.state_samples[0], sensitivity=2.),))
            for context, reason in ((replace(recorded, state_samples=()), "missing_state_samples"), (invalid, "invalid_sensitivity")):
                with self.assertRaises(GenerationCaseError) as caught:
                    backend.prepare_case(recorded.event.session_id, context, eye_height=1.62)
                self.assertEqual(reason, caught.exception.reason)

    def test_close_releases_java_process(self):
        client = JavaMinerClient()
        client.close()
        self.assertEqual(0, client._process.returncode)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            client.angular_step_deg(.5)
        client.close()


if __name__ == "__main__":
    unittest.main()
