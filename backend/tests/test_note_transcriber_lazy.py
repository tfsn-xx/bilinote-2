import unittest
from unittest.mock import patch

from app.services.note import NoteGenerator


class TestNoteTranscriberLazyInit(unittest.TestCase):
    def test_constructor_does_not_initialize_whisper(self):
        with patch.object(
            NoteGenerator,
            "_init_transcriber",
            side_effect=AssertionError("Whisper must stay lazy"),
        ):
            generator = NoteGenerator()
        self.assertIsNone(generator.transcriber)

    def test_fallback_initializes_configured_base_model_once(self):
        generator = NoteGenerator()
        generator.transcriber_type = "fast-whisper"
        generator.model_size = "base"
        fake_transcriber = object()

        with patch("app.services.note.get_transcriber", return_value=fake_transcriber) as factory:
            self.assertIs(generator._get_or_init_transcriber(), fake_transcriber)
            self.assertIs(generator._get_or_init_transcriber(), fake_transcriber)

        factory.assert_called_once_with(
            transcriber_type="fast-whisper",
            model_size="base",
            device="cpu",
        )


if __name__ == "__main__":
    unittest.main()
