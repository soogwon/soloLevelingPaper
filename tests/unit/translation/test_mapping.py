from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from solo_leveling.domain.models import Chunk
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.application.translation.mapping import translated_chunks
from solo_leveling.infrastructure.translation.fake_provider import FakeTranslationProvider


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.sources = (
            Chunk("c1", "p1", 0, "First.", "이전 번역 1", "old", "iv", 2, "method"),
            Chunk("c2", "p1", 1, "Second.", "이전 번역 2", "old", "v", 3, "results"),
        )
        self.settings = TranslationSettings("fake", "fixture", "prompt1")

    def batch(self, failures=()):
        return TranslationService(FakeTranslationProvider(
            {"First.": "첫째.", "Second.": "둘째."}, failures,
        ), lambda: "new").translate(self.sources, self.settings)

    def test_new_chunks_preserve_original_and_metadata(self):
        before = tuple(replace(c) for c in self.sources)
        mapped = translated_chunks(self.sources, self.batch())
        self.assertEqual(self.sources, before)
        for source, new in zip(self.sources, mapped):
            self.assertIsNot(source, new)
            self.assertEqual(replace(new, text=source.text, translation_revision_id=source.translation_revision_id), source)
            self.assertEqual(new.translation_revision_id, "new")
        self.assertEqual(mapped[0].text, "첫째.")

    def test_partial_failure_keeps_old_translation_and_excludes_failed_chunk(self):
        batch = self.batch(("c2",))
        mapped = translated_chunks(self.sources, batch)
        self.assertEqual([c.chunk_id for c in mapped], ["c1"])
        self.assertEqual(self.sources[1].text, "이전 번역 2")
        self.assertEqual(self.sources[1].translation_revision_id, "old")
        self.assertIsNone(batch.items[1].text)

    def test_total_failure_returns_no_index_candidates(self):
        self.assertEqual(translated_chunks(self.sources, self.batch(("c1", "c2"))), ())

    def test_mismatched_sources_rejected(self):
        batch = self.batch()
        invalid = (
            self.sources[:1], (self.sources[0], self.sources[0]),
            (replace(self.sources[0], original_text="Changed."), self.sources[1]),
            (replace(self.sources[0], parse_revision_id="p2"), self.sources[1]),
        )
        for sources in invalid:
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                translated_chunks(sources, batch)

    def test_mutated_revision_metadata_rechecked(self):
        batch = self.batch()
        batch.revision.model = "different-model"
        with self.assertRaises(ValueError):
            translated_chunks(self.sources, batch)

    def test_provider_receives_snapshot_requests(self):
        sources = self.sources

        class MutatingProvider:
            def translate(self, request, settings):
                sources[1].original_text = "changed during call"
                return "번역"

        batch = TranslationService(MutatingProvider()).translate(sources, self.settings)
        self.assertEqual(batch.items[1].request.original_text, "Second.")
        with self.assertRaises(ValueError):
            translated_chunks(sources, batch)


if __name__ == "__main__":
    unittest.main()
