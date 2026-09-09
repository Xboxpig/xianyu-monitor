from src.scraper import _classify_playwright_timeout, _log_classified_timeout


def test_classifies_initial_search_response_timeout():
    error = TimeoutError('Timeout 30000ms exceeded while waiting for event "response"')

    assert _classify_playwright_timeout(error, "search_initial_response") == (
        "SEARCH_RESPONSE_TIMEOUT",
        "network_response",
    )


def test_classifies_search_filter_selector_timeout():
    error = TimeoutError(
        'Page.wait_for_selector: Timeout 15000ms exceeded. '
        'waiting for locator("text=新发布") to be visible'
    )

    assert _classify_playwright_timeout(error, "search_filter_readiness") == (
        "SEARCH_FILTER_SELECTOR_TIMEOUT",
        "page_readiness",
    )


def test_classifies_risk_control_overlay_interception_first():
    error = TimeoutError(
        '<div class="baxia-dialog-mask"></div> intercepts pointer events'
    )

    assert _classify_playwright_timeout(error, "filter_application") == (
        "RISK_CONTROL_OVERLAY_BLOCKED",
        "risk_control",
    )


def test_classifies_navigation_and_generic_timeouts():
    assert _classify_playwright_timeout(
        TimeoutError("Page.goto: Navigation timeout"),
        "home_navigation",
    ) == ("PAGE_NAVIGATION_TIMEOUT", "navigation")
    assert _classify_playwright_timeout(
        TimeoutError("locator click timed out"),
        "filter_application",
    ) == ("PLAYWRIGHT_OPERATION_TIMEOUT", "playwright_timeout")


def test_timeout_log_contains_machine_marker_and_runtime_context(capsys):
    error = TimeoutError('Timeout 30000ms exceeded while waiting for event "response"')

    code = _log_classified_timeout(
        error,
        stage="search_initial_response",
        task_name="ps5-bxl",
        account_strategy="fixed",
        state_file="state/123.json",
        page_url="https://www.goofish.com/search?q=ps5+slim",
        attempt_number=1,
        max_attempts=2,
    )
    output = capsys.readouterr().out

    assert code == "SEARCH_RESPONSE_TIMEOUT"
    assert '"code": "SEARCH_RESPONSE_TIMEOUT"' in output
    assert '"task": "ps5-bxl"' in output
    assert "[错误分类] network_response" in output
    assert "[错误阶段] search_initial_response" in output
    assert "[尝试次数] 1/2" in output
    assert "策略=fixed, 登录状态=state/123.json" in output
    assert "Timeout 30000ms" in output
