from unittest import TestCase

from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from alves_ia.ai import GeminiProvider, OpenAIProvider, create_provider
from alves_ia.config import Settings


class SettingsForTests(Settings):
    model_config = SettingsConfigDict(env_file=None)


class ProviderSelectionTests(TestCase):
    def test_gemini_is_the_default_provider(self) -> None:
        settings = SettingsForTests(
            ai_provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash",
            openai_api_key="",
            openai_model="gpt-4o-mini",
        )

        provider = create_provider(settings)

        self.assertIsInstance(provider, GeminiProvider)
        self.assertEqual(provider.model, "gemini-2.5-flash")

    def test_openai_can_be_selected_as_an_alternative(self) -> None:
        settings = SettingsForTests(
            ai_provider="openai",
            gemini_api_key="",
            gemini_model="gemini-2.5-flash",
            openai_api_key="test-key",
            openai_model="gpt-4o-mini",
        )

        provider = create_provider(settings)

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model, "gpt-4o-mini")

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(ValidationError) as error:
            SettingsForTests.model_validate({"ai_provider": "unknown"})

        self.assertEqual(error.exception.errors()[0]["loc"], ("ai_provider",))
        self.assertEqual(error.exception.errors()[0]["type"], "literal_error")
