from app import agent
from app.checks import claimed_cpu_percent, cpu_check, measured_cpu_cores

METRICS = (
    "[up]\n{'job': 'prometheus'} => 1\n"
    "[cpu_rate_5m]\n{'job': 'prometheus'} => 0.0022\n"
    "[resident_memory_bytes]\n{'job': 'prometheus'} => 123456"
)


def test_claimed_percent_found_in_annotations():
    assert claimed_cpu_percent("HighCPU", {"summary": "CPU at 90%"}) == 90.0


def test_claimed_percent_ignored_without_cpu():
    assert claimed_cpu_percent("DiskFull", {"summary": "Disk at 95%"}) is None


def test_measured_cores_reads_only_cpu_section():
    assert measured_cpu_cores(METRICS) == 0.0022


def test_measured_cores_handles_no_data():
    assert measured_cpu_cores("[cpu_rate_5m]\nno data") is None


def test_mismatch_reports_both_numbers():
    line = cpu_check("HighCPU", {"summary": "CPU at 90%"}, METRICS)
    assert line.startswith("CHECK: MISMATCH")
    assert "90% CPU" in line and "0.22%" in line


def test_consistent_when_close():
    metrics = "[cpu_rate_5m]\n{'job': 'x'} => 0.88"
    assert cpu_check("HighCPU", {"summary": "CPU at 90%"}, metrics).startswith(
        "CHECK: CONSISTENT"
    )


def test_not_applicable_without_data():
    line = cpu_check("HighCPU", {"summary": "CPU at 90%"}, "[cpu_rate_5m]\nno data")
    assert line.startswith("CHECK: NOT APPLICABLE")


def test_gather_evidence_includes_check(monkeypatch):
    monkeypatch.setattr(agent, "collect_metrics", lambda labels: METRICS)
    monkeypatch.setattr(agent, "collect_logs", lambda labels: "no log lines")
    state = {"alertname": "HighCPU", "annotations": {"summary": "CPU at 90%"}}
    assert "CHECK: MISMATCH" in agent.gather_evidence(state)["evidence"]


def test_contradiction_sentence_uses_code_numbers():
    from app.checks import contradiction_sentence

    line = "CHECK: MISMATCH. Alert claims 90% CPU, measured 0.07% of one core."
    sentence = contradiction_sentence(f"METRICS:\n...\n\n{line}\n\nLOGS:\n...")
    assert sentence.startswith("The evidence contradicts the alert:")
    assert "90% CPU" in sentence and "0.07%" in sentence


def test_contradiction_sentence_none_when_consistent():
    from app.checks import contradiction_sentence

    assert contradiction_sentence("CHECK: CONSISTENT. Alert claims 90% CPU") is None


def test_analyze_prepends_code_sentence(monkeypatch):
    monkeypatch.setattr(
        agent,
        "ask_llm",
        lambda prompt: "ROOT CAUSE: Check the alert rule.\nREMEDIATION: 1. Verify rule.",
    )
    evidence = "CHECK: MISMATCH. Alert claims 90% CPU, measured 0.07% of one core."
    result = agent.analyze({"evidence": evidence})
    assert result["root_cause"].startswith("The evidence contradicts the alert:")
    assert "0.07%" in result["root_cause"]
    assert "Check the alert rule." in result["root_cause"]
