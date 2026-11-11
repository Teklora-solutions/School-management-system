"""Guard the shared styling cleanup and locally available public-site icons."""

import html
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = Path(settings.TEMPLATES[0]["DIRS"][0])
STATIC = Path(settings.STATICFILES_DIRS[0])
# Decode numeric HTML entities too: an encoded emoji is still an emoji.
EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]")
ICON_CLASS = re.compile(r"\bbi-([a-z0-9-]+)")


class IconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "bi" in attrs.get("class", "").split():
            self.icons.append(attrs)


class StylingContractTests(SimpleTestCase):
    def test_application_templates_do_not_use_emoji_icons(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            with self.subTest(template=str(path.relative_to(TEMPLATES))):
                self.assertIsNone(EMOJI.search(html.unescape(path.read_text(encoding="utf-8"))))

    def test_public_icons_exist_in_the_local_icon_font(self):
        stylesheet = (STATIC / "vendor/bootstrap-icons/1.11.3/bootstrap-icons.min.css").read_text(encoding="utf-8")
        for path in sorted((TEMPLATES / "public").glob("*.html")):
            icons = set(ICON_CLASS.findall(path.read_text(encoding="utf-8")))
            for icon in icons:
                with self.subTest(template=path.name, icon=icon):
                    self.assertIn(f".bi-{icon}::before", stylesheet)

    def test_public_decorative_icons_are_hidden_from_screen_readers(self):
        for path in sorted((TEMPLATES / "public").glob("*.html")):
            parser = IconParser()
            parser.feed(path.read_text(encoding="utf-8"))
            for attrs in parser.icons:
                with self.subTest(template=path.name, icon=attrs["class"]):
                    self.assertEqual(attrs.get("aria-hidden"), "true")

    def test_shared_shells_no_longer_embed_static_style_attributes(self):
        for name in (
            "base.html",
            "dashboards/admin/home.html",
            "partials/_messages.html",
            "partials/_pagination.html",
            "public/base.html",
            "public/home.html",
        ):
            with self.subTest(template=name):
                self.assertNotRegex((TEMPLATES / name).read_text(encoding="utf-8"), r"\bstyle\s*=")

    def test_public_layout_loads_the_local_icon_stylesheet(self):
        source = (TEMPLATES / "public/base.html").read_text(encoding="utf-8")
        self.assertIn("{% static 'vendor/bootstrap-icons/1.11.3/bootstrap-icons.min.css' %}", source)
        self.assertIn('aria-label="Switch to light mode"', source)
        self.assertIn("themeBtn.querySelector('i').className", source)
