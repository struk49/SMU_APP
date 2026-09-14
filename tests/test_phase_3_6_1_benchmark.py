from scripts import generate_phase_3_6_1_real_provider_benchmark as benchmark


def test_default_benchmark_mode_is_zero_call(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["benchmark"])
    monkeypatch.setattr(
        benchmark, "run_real",
        lambda: (_ for _ in ()).throw(AssertionError("provider called")),
    )
    benchmark.main()
    output = capsys.readouterr().out
    assert "TOTAL: 8 provider calls" in output
    assert "--real-provider" in output


def test_benchmark_matrix_is_exact_and_fixed():
    matrix = benchmark.benchmark_matrix()
    assert matrix["total_calls"] == 8
    assert len(matrix["style_calls"]) == 6
    assert len(matrix["model_calls"]) == 2
    assert {item[2] for item in matrix["style_calls"]} == {"smu_classic"}
