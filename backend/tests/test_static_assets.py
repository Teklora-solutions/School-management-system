"""
backend/tests/test_static_assets.py
-----------------------------------
Module 3 S0b — the app must render without the internet.

Three failure modes this guards:
  * a template reaching for a CDN again (the page looks fine online and is
    unstyled offline),
  * a ``{% static %}`` path that points at a file which does not exist. That
    second one is the expensive one: ``config.settings.test`` swaps the
    manifest storage for plain ``StaticFilesStorage``, so the suite passes on a
    typo that makes every page 500 in dev and prod, where
    ``CompressedManifestStaticFilesStorage`` raises ``ValueError`` on render,
  * a vendored file pointing at a sibling it does not ship (``.map`` files),
    which makes ``collectstatic`` itself abort.

The font allowlist below works like ``KNOWN_BROKEN`` in
``apps/core/tests/test_navigation_integrity.py`` — it records a known external
dependency with the reason, and may only shrink.

One deliberate edit to the vendor copies: the trailing ``sourceMappingURL``
comment was stripped from ``bootstrap.min.css``, ``bootstrap.bundle.min.js`` and
``chart.umd.min.js``. Re-downloading those files overwrites that fix, so the
last test here fails rather than letting ``collectstatic`` break silently.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

TEMPLATES_DIR = Path(settings.TEMPLATES[0]["DIRS"][0])
STATIC_DIR = settings.STATICFILES_DIRS[0]

# External URL fragment → why it is still allowed.
EXTERNAL_ALLOWLIST = {
    "fonts.googleapis.com": (
        "Inter / Plus Jakarta Sans are still fetched from Google Fonts by "
        "css/base.css; offline the text falls back to system fonts instead of "
        "breaking the layout, so this is cosmetic. Localise it with the next "
        "static-asset pass."
    ),
}

# <script src="http..."> / <link href="http..."> — the two tags that gate a render.
REMOTE_TAG_RE = re.compile(
    r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["']https?://""",
    re.IGNORECASE,
)
STATIC_TAG_RE = re.compile(r"""{%\s*static\s+['"]([^'"]+)['"]\s*%}""")
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""")
SOURCE_MAP_RE = re.compile(r"""sourceMappingURL=([^\s*'")]+)""")


def _templates() -> list[Path]:
    return sorted(TEMPLATES_DIR.rglob("*.html"))


def _static_files(suffix: str) -> list[Path]:
    # rglob matches directories by name too ("vendor/chart.js" fits "*.js"),
    # so keep real files only.
    return [p for p in sorted(Path(STATIC_DIR).rglob(f"*{suffix}")) if p.is_file()]


def _allowed(url_text: str) -> bool:
    return any(fragment in url_text for fragment in EXTERNAL_ALLOWLIST)


class NoRemoteAssetsTest(SimpleTestCase):
    def test_no_template_loads_a_script_or_stylesheet_from_a_cdn(self):
        offenders = []
        for path in _templates():
            for line in path.read_text(encoding="utf-8").splitlines():
                if REMOTE_TAG_RE.search(line) and not _allowed(line):
                    offenders.append(f"{path.relative_to(TEMPLATES_DIR)}: {line.strip()}")
        if offenders:
            self.fail(
                "Templates load assets from a remote host — vendor them instead:\n"
                + "\n".join(f"  {o}" for o in offenders)
            )

    def test_the_allowlist_only_describes_what_is_actually_still_remote(self):
        """
        A stale allowlist entry hides a dependency that has since gone away, so
        each fragment must still appear somewhere in the frontend.
        """
        body = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [*_templates(), *_static_files(".css"), *_static_files(".js")]
        )
        for fragment, reason in EXTERNAL_ALLOWLIST.items():
            self.assertIn(
                fragment,
                body,
                msg=f"allowlist entry is stale, delete it: {fragment} ({reason})",
            )


class StaticPathsExistTest(SimpleTestCase):
    def test_every_static_tag_in_a_template_resolves(self):
        missing = []
        for path in _templates():
            text = path.read_text(encoding="utf-8")
            for asset in STATIC_TAG_RE.findall(text):
                if finders.find(asset) is None:
                    missing.append(
                        f"{path.relative_to(TEMPLATES_DIR)} → {asset!r}"
                    )
        if missing:
            self.fail(
                "{% static %} points at files that do not exist "
                "(collectstatic and every page render will fail):\n"
                + "\n".join(f"  {m}" for m in missing)
            )

    def test_vendored_css_only_references_assets_that_ship_with_it(self):
        """
        Bootstrap Icons' stylesheet loads its font by a relative URL, so the
        font has to sit next to the stylesheet exactly as the package ships it.
        """
        broken = []
        for stylesheet in _static_files(".css"):
            text = stylesheet.read_text(encoding="utf-8", errors="ignore")
            for ref in CSS_URL_RE.findall(text):
                if ref.startswith(("data:", "http://", "https://", "#")):
                    continue
                target = (stylesheet.parent / ref.split("?")[0]).resolve()
                if not target.is_file():
                    broken.append(f"{stylesheet.name} → {ref} (missing {target})")
        if broken:
            self.fail(
                "CSS references an asset that is not on disk:\n"
                + "\n".join(f"  {b}" for b in broken)
            )

    def test_vendored_files_declare_no_source_maps_we_do_not_ship(self):
        """
        The minified vendor bundles end with a ``sourceMappingURL`` comment. The
        ``.map`` files are deliberately not downloaded (~1.5 MB of dev-only
        source), and ``ManifestFilesMixin.post_process()`` resolves that comment
        during ``collectstatic`` — a missing target raises ``MissingFileError``
        and aborts the whole run, so the manifest is never rewritten and every
        page under dev/prod settings 500s.
        """
        dangling = []
        for path in _static_files(".css") + _static_files(".js"):
            if "vendor" not in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for ref in SOURCE_MAP_RE.findall(text):
                if ref.startswith(("http://", "https://")):
                    continue
                if not (path.parent / ref.split("?")[0]).is_file():
                    dangling.append(f"{path.relative_to(STATIC_DIR)} → {ref}")
        if dangling:
            self.fail(
                "Vendored files reference source maps that are not on disk; "
                "either ship the .map or strip the comment\n"
                "(see the vendor-files note in this module's docstring):\n"
                + "\n".join(f"  {d}" for d in dangling)
            )
