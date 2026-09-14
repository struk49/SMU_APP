from scripts import benchmark_phase_3_6_5_typography_preflight as benchmark


def test_six_slide_benchmark_reduces_measurement_calls_without_providers():
    result = benchmark.profile_once()

    assert len(result["results"]) == 6
    assert all(item["fits"] for item in result["results"])
    assert result["line_width_calls"] < 1000
    assert result["textbbox_calls"] < 1200
    assert result["candidate_font_attempts"] < 150
    assert 0 < result["cache_misses"] <= result["line_width_calls"]
    assert "openai" not in benchmark.profile_once.__code__.co_names
