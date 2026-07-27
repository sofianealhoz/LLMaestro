import tempfile
import unittest
from pathlib import Path

from llmaestro.config import DEFAULT_CATALOGUE, load_catalogue, load_env

CATALOGUE = """
[[provider]]
name = "paid"
kind = "openai_compat"
base_url = "https://api.example.com/v1"
model = "small"
env_key = "PAID_KEY"
cost = 2

[[provider]]
name = "local"
kind = "ollama"
base_url = "http://localhost:11434"
model = "qwen2.5-coder:7b"

[[provider]]
name = "retired"
kind = "openai_compat"
base_url = "https://api.example.com/v1"
model = "old"
enabled = false
"""


def write(text: str, name: str = "providers.toml") -> str:
    directory = tempfile.mkdtemp()
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class Catalogue(unittest.TestCase):
    def test_a_provider_without_its_key_is_skipped_with_a_reason(self):
        ready, skipped = load_catalogue(write(CATALOGUE), environ={})

        self.assertEqual(["local"], [spec.name for spec in ready])
        reasons = dict(skipped)
        self.assertIn("PAID_KEY", reasons["paid"])
        self.assertIn("disabled", reasons["retired"])

    def test_a_key_in_the_environment_makes_the_provider_usable(self):
        ready, _ = load_catalogue(write(CATALOGUE), environ={"PAID_KEY": "secret"})

        paid = next(spec for spec in ready if spec.name == "paid")
        self.assertEqual("secret", paid.api_key)

    def test_ollama_host_overrides_the_catalogue(self):
        ready, _ = load_catalogue(
            write(CATALOGUE), environ={"OLLAMA_HOST": "http://kali:11434"}
        )

        local = next(spec for spec in ready if spec.name == "local")
        self.assertEqual("http://kali:11434", local.base_url)

    def test_an_unknown_kind_is_refused_loudly(self):
        broken = """
        [[provider]]
        name = "weird"
        kind = "telepathy"
        base_url = "x"
        model = "y"
        """
        with self.assertRaises(ValueError):
            load_catalogue(write(broken), environ={})

    def test_a_missing_field_is_refused_loudly(self):
        broken = """
        [[provider]]
        name = "half"
        kind = "ollama"
        """
        with self.assertRaises(ValueError):
            load_catalogue(write(broken), environ={})

    def test_the_shipped_catalogue_is_valid(self):
        ready, skipped = load_catalogue(DEFAULT_CATALOGUE, environ={})

        names = [spec.name for spec in ready] + [name for name, _ in skipped]
        for expected in ("cerebras", "groq", "openrouter", "ollama"):
            self.assertIn(expected, names)
        vision = [spec.name for spec in ready if spec.vision]
        self.assertTrue(vision, "the catalogue should ship a vision-capable provider")


class Env(unittest.TestCase):
    def test_the_environment_wins_over_the_file(self):
        path = write("EXISTING=from-file\nFRESH=from-file\n", name=".env")
        environ = {"EXISTING": "exported"}

        load_env(path, environ=environ)

        self.assertEqual("exported", environ["EXISTING"])
        self.assertEqual("from-file", environ["FRESH"])

    def test_comments_blanks_and_quotes_are_handled(self):
        path = write('# a comment\n\nQUOTED="value"\nBARE = plain\nEMPTY=\n', name=".env")
        environ = {}

        loaded = load_env(path, environ=environ)

        self.assertEqual({"QUOTED": "value", "BARE": "plain"}, loaded)
        self.assertNotIn("EMPTY", environ)

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual({}, load_env("does-not-exist.env", environ={}))


class ModelServed(unittest.TestCase):
    def test_a_namespaced_listing_still_matches(self):
        from llmaestro.cli import _is_served

        google = ["models/gemini-3.6-flash", "models/gemini-2.0-flash"]
        self.assertTrue(_is_served("gemini-3.6-flash", google))
        self.assertFalse(_is_served("gemini-9-flash", google))

    def test_a_meaningful_prefix_is_not_ignored(self):
        from llmaestro.cli import _is_served

        github = ["openai/gpt-4.1-mini", "meta/llama-4"]
        self.assertTrue(_is_served("openai/gpt-4.1-mini", github))
        self.assertFalse(_is_served("gpt-4.1-mini", github), "le prefixe fait partie du nom")


if __name__ == "__main__":
    unittest.main()
