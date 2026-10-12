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


@pytest.fixture(scope="module")
def collector_runner():
    repo = Path(__file__).resolve().parents[2]
    source = (repo / "chrome-extension/collector.js").read_text(encoding="utf-8")
    source = source[source.index("export async function interactPage"):source.index("export function collectSnapshot")]
    source = source.replace("export async", "async", 1)
    return "async options => { " + source + '; return await interactPage("filters", options); }'


@pytest.mark.parametrize("label", ["最新", "1天内", "3天内", "7天内", "14天内"])
def test_hidden_publication_dropdown_selects_once_without_renderer_timers(chrome_browser, collector_runner, label):
    repo = Path(__file__).resolve().parents[2]
    fixture = (repo / "chrome-extension/tests/fixtures/publication-dropdown.html").read_text(encoding="utf-8")
    page = chrome_browser.new_page()
    try:
        page.set_content(fixture)
        page.evaluate('() => { window.setTimeout = () => { throw new Error("renderer timer used"); }; }')
        assert not page.locator('[class*="search-select-item--"]').first.is_visible()
        result = page.evaluate(collector_runner, {"new_publish_option": label})
        if result.get("pending"):
            result = page.evaluate(collector_runner, {"new_publish_option": label})
        assert not result.get("pending") and not result.get("retry"), result
        assert page.locator('[class*="search-select-title--"]').inner_text() == label
        assert page.evaluate("window.clicks") == 1
        assert not page.evaluate(collector_runner, {"new_publish_option": label})["changed"]
        assert page.evaluate("window.clicks") == 1
    finally:
        page.close()


@pytest.mark.parametrize("minimum,maximum,desired,request_range", [
    ("2500", "3200", ["2500", "3200"], "priceRange:2500,3200;"),
    ("2500", None, ["2500", ""], "priceRange:2500,undefined;"),
    (None, "3200", ["", "3200"], "priceRange:0,3200;"),
    (0, 3200.0, ["", "3200"], "priceRange:0,3200;"),
])
def test_price_inputs_submit_controlled_values_and_wait_for_matching_results(
    chrome_browser, collector_runner, minimum, maximum, desired, request_range,
):
    repo = Path(__file__).resolve().parents[2]
    fixture = (repo / "chrome-extension/tests/fixtures/price-filter.html").read_text(encoding="utf-8")
    page = chrome_browser.new_page()
    try:
        page.set_content(fixture)
        # Start with stale values so a one-sided range must clear the other end.
        page.locator('input').nth(0).fill('100')
        page.locator('input').nth(1).fill('9000')
        page.evaluate('() => { window.setTimeout = () => { throw new Error("renderer timer used"); }; }')
        options = {"min_price": minimum, "max_price": maximum}
        filled = page.evaluate(collector_runner, options)
        assert filled["pending"] and not filled["changed"]
        assert page.locator('input').evaluate_all('(inputs) => inputs.map(input => input.value)') == desired
        submitted = page.evaluate(collector_runner, options)
        assert submitted["pending"] and submitted["changed"]
        assert page.evaluate('window.priceSubmissions') == [request_range]
        floor = submitted["sequenceFloor"]
        page.evaluate("window.priceResponse('priceRange:100,9000;')")
        assert page.evaluate(collector_runner, options)["pending"], "unrelated search response must not finish price filtering"
        page.evaluate("window.priceResponse(null, false)")
        assert page.evaluate(collector_runner, options)["pending"], "failed search must not confirm price filtering"
        page.evaluate("window.priceResponse()")
        applied = page.evaluate(collector_runner, options)
        assert not applied.get("pending") and not applied.get("retry"), applied
        assert page.evaluate('window.__xianyuNativeCapture.sequence') > floor
        assert not page.evaluate(collector_runner, options)["changed"]
        assert page.evaluate('window.priceSubmissions') == [request_range]
    finally:
        page.close()


@pytest.mark.parametrize("options", [{}, {"min_price": "", "max_price": ""}, {"min_price": 0}])
def test_unbounded_price_does_not_require_or_submit_price_inputs(chrome_browser, collector_runner, options):
    page = chrome_browser.new_page()
    try:
        page.set_content('<div>search</div>')
        result = page.evaluate(collector_runner, options)
        assert not result.get("pending") and not result.get("retry") and not result["changed"]
    finally:
        page.close()


def test_missing_price_controls_request_bounded_retry(chrome_browser, collector_runner):
    page = chrome_browser.new_page()
    try:
        page.set_content('<div>search</div>')
        result = page.evaluate(collector_runner, {"min_price": "2500", "max_price": "3200"})
        assert result["retry"] and "页面价格筛选未找到" in result["error"]
    finally:
        page.close()
