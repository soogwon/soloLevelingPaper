"""Run with unittest discover -s tests/unit/translation -v."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from solo_leveling.domain.models import Chunk
from solo_leveling.domain.translation import TranslationFailureCode, TranslationSettings
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.application.translation.ports import TranslationProviderUnavailable
from solo_leveling.infrastructure.translation.fake_provider import FakeTranslationProvider


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.chunks = (Chunk("c1", "p1", 0, "Attention."), Chunk("c2", "p1", 1, "Results."))
        self.settings = TranslationSettings("fake", "fixture-v1", "prompt-v1")
        self.provider = FakeTranslationProvider({"Attention.": "어텐션.", "Results.": "결과."})

    def test_success_shares_revision_and_metadata(self):
        result = TranslationService(self.provider).translate(self.chunks, self.settings)
        self.assertEqual((result.success_count, result.failure_count), (2, 0))
        self.assertEqual(result.revision.parse_revision_id, "p1")
        self.assertEqual(result.revision.provider, "fake")
        self.assertEqual(result.settings.prompt_version, "prompt-v1")
        self.assertEqual([r.text for r in result.items], ["어텐션.", "결과."])
        self.assertTrue(all(c.text is None for c in self.chunks))

    def test_partial_and_total_failure(self):
        for ids in (("c1",), ("c1", "c2")):
            provider = FakeTranslationProvider(self.provider.translations, ids)
            result = TranslationService(provider).translate(self.chunks, self.settings)
            self.assertEqual(result.failure_count, len(ids))
            self.assertEqual(len(provider.calls), 2)
            for item in result.items:
                if item.request.chunk_id in ids:
                    self.assertIsNone(item.text)
                    self.assertEqual(item.failure_code, TranslationFailureCode.PROVIDER_UNAVAILABLE)

    def test_empty_translation_is_failure(self):
        provider = FakeTranslationProvider({"Attention.": "   ", "Results.": "결과."})
        result = TranslationService(provider).translate(self.chunks, self.settings)
        self.assertEqual(result.items[0].failure_code, TranslationFailureCode.EMPTY_TRANSLATION)
        self.assertIsNone(result.items[0].text)

    def test_invalid_batch_makes_no_calls(self):
        bad_batches = ((), (self.chunks[0], self.chunks[0]),
                       (self.chunks[0], replace(self.chunks[1], parse_revision_id="p2")),
                       (self.chunks[0], replace(self.chunks[1], original_text=" ")),
                       (replace(self.chunks[0], chunk_id=""),))
        for chunks in bad_batches:
            with self.subTest(chunks=chunks), self.assertRaises(ValueError):
                TranslationService(self.provider).translate(chunks, self.settings)
        self.assertEqual(self.provider.calls, [])

    def test_retranslation_gets_new_id(self):
        service = TranslationService(self.provider)
        first = service.translate(self.chunks, self.settings)
        second = service.translate(self.chunks, self.settings)
        self.assertNotEqual(first.revision.translation_revision_id, second.revision.translation_revision_id)
        prior = (replace(self.chunks[0], text="기존 번역", translation_revision_id="old"),)
        with self.assertRaises(ValueError):
            TranslationService(self.provider, lambda: "old").translate(prior, self.settings)

    def test_unexpected_error_and_wrong_return_type_propagate(self):
        class Broken:
            def translate(self, request, settings):
                raise RuntimeError("adapter bug")

        class WrongType:
            def translate(self, request, settings):
                return None

        for provider, error in ((Broken(), RuntimeError), (WrongType(), TypeError)):
            with self.subTest(provider=provider), self.assertRaises(error):
                TranslationService(provider).translate(self.chunks, self.settings)

    def test_expected_failure_does_not_expose_exception_text(self):
        class Unavailable:
            def translate(self, request, settings):
                raise TranslationProviderUnavailable("secret-api-key")

        result = TranslationService(Unavailable()).translate(self.chunks, self.settings)
        self.assertNotIn("secret-api-key", repr(result))
        self.assertEqual(result.failure_count, 2)

    def test_unchanged_symbols_are_valid(self):
        provider = FakeTranslationProvider({"x = y": "x = y"})
        result = TranslationService(provider).translate((Chunk("c", "p", 0, "x = y"),), self.settings)
        self.assertEqual(result.success_count, 1)

    def test_fake_missing_fixture_does_not_fallback_to_source(self):
        with self.assertRaises(ValueError):
            TranslationService(FakeTranslationProvider({})).translate(self.chunks, self.settings)


if __name__ == "__main__":
    unittest.main()
