import re

PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def claimed_cpu_percent(alertname: str, annotations: dict) -> float | None:
    text = " ".join([alertname, *(str(v) for v in annotations.values())])
    if "cpu" not in text.lower():
        return None
    match = PERCENT.search(text)
    return float(match.group(1)) if match else None


def measured_cpu_cores(metrics: str) -> float | None:
    values = []
    in_cpu_section = False
    for line in metrics.splitlines():
        if line.startswith("["):
            in_cpu_section = line.strip() == "[cpu_rate_5m]"
        elif in_cpu_section and "=>" in line:
            try:
                values.append(float(line.rsplit("=>", 1)[1]))
            except ValueError:
                pass
    return max(values) if values else None


def cpu_check(alertname: str, annotations: dict, metrics: str) -> str:
    claimed = claimed_cpu_percent(alertname, annotations)
    cores = measured_cpu_cores(metrics)
    if claimed is None or cores is None:
        return "CHECK: NOT APPLICABLE (no CPU figure in the alert or no CPU data)."
    measured = cores * 100
    close = claimed * 0.5 <= measured <= claimed * 1.5 or abs(measured - claimed) < 5
    verdict = "CONSISTENT" if close else "MISMATCH"
    return (
        f"CHECK: {verdict}. Alert claims {claimed:g}% CPU, "
        f"measured {measured:.2f}% of one core."
    )
