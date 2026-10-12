"""Exercise the serialized extension against Goofish's CSS dropdown structure."""
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def chrome_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    executable = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not executable.exists():
        pytest.skip("Requires installed Windows Chrome; this test never downloads a browser")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=str(executable), headless=True)
        yield browser
        browser.close()


@pytest.mark.parametrize("label", ["最新", "1天内", "3天内", "7天内", "14天内"])
def test_hidden_publication_dropdown_selects_once_without_renderer_timers(chrome_browser, label):
    repo = Path(__file__).resolve().parents[2]
    source = (repo / "chrome-extension/collector.js").read_text(encoding="utf-8")
    source = source[source.index("export async function interactPage"):source.index("export function collectSnapshot")]
    source = source.replace("export async", "async", 1)
    runner = "async options => { " + source + '; return await interactPage("filters", options); }'
    fixture = (repo / "chrome-extension/tests/fixtures/publication-dropdown.html").read_text(encoding="utf-8")
    page = chrome_browser.new_page()
    try:
        page.set_content(fixture)
        page.evaluate('() => { window.setTimeout = () => { throw new Error("renderer timer used"); }; }')
        assert not page.locator('[class*="search-select-item--"]').first.is_visible()
        result = page.evaluate(runner, {"new_publish_option": label})
        if result.get("pending"):
            result = page.evaluate(runner, {"new_publish_option": label})
        assert not result.get("pending") and not result.get("retry"), result
        assert page.locator('[class*="search-select-title--"]').inner_text() == label
        assert page.evaluate("window.clicks") == 1
        assert not page.evaluate(runner, {"new_publish_option": label})["changed"]
        assert page.evaluate("window.clicks") == 1
    finally:
        page.close()
