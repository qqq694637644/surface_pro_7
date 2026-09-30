from sp7_powerlab.demand import DemandObserver, remote_process_tags


def sample(**changes):
    base = {
        "ts": 1.0,
        "user_active": True,
        "cpu_usage": 5.0,
        "cpu_psi": 0.0,
        "io_psi": 0.0,
        "load1": 0.2,
        "rapl_power_60s_w": 2.0,
        "network_rx_mbps": 0.1,
        "network_tx_mbps": 0.05,
        "media_playing": False,
        "processes": [],
    }
    base.update(changes)
    return base


def test_idle_demand_is_low_latency():
    result = DemandObserver(cpu_count=4).observe(sample(user_active=False))
    assert result["latency_need"] == "LOW"
    assert result["local_compute_pressure"] == "LOW"


def test_active_short_work_is_interactive_not_sustained():
    result = DemandObserver(cpu_count=4).observe(sample(cpu_usage=18.0))
    assert result["latency_need"] == "MEDIUM"
    assert result["local_compute_pressure"] == "LOW"


def test_high_cpu_becomes_sustained_compute():
    result = DemandObserver(cpu_count=4).observe(
        sample(cpu_usage=80.0, rapl_power_60s_w=12.0, load1=4.0)
    )
    assert result["local_compute_pressure"] == "SUSTAINED"
    assert result["latency_need"] == "HIGH"


def test_remote_hint_uses_process_and_network():
    result = DemandObserver(cpu_count=4).observe(
        sample(
            network_rx_mbps=2.0,
            processes=[{"name": "ssh"}],
        )
    )
    assert result["remote_hint"] >= 0.5
    assert result["network_intensity"] == "INTERACTIVE"


def test_remote_hint_does_not_depend_on_top_cpu_process_list():
    result = DemandObserver(cpu_count=4).observe(
        sample(
            network_rx_mbps=2.0,
            processes=[],
            remote_process_present=True,
        )
    )
    assert result["remote_hint"] >= 0.5
    assert "REMOTE" in result["region"]


def test_ssh_agent_is_not_treated_as_remote_session():
    assert remote_process_tags("ssh-agent", "/usr/bin/ssh-agent") == set()
    result = DemandObserver(cpu_count=4).observe(
        sample(
            network_rx_mbps=0.1,
            processes=[{"name": "ssh-agent", "executable": "/usr/bin/ssh-agent"}],
        )
    )
    assert result["remote_hint"] < 0.5
    assert "LOCAL" in result["region"]


def test_exact_ssh_process_is_remote_even_when_low_cpu():
    assert remote_process_tags("ssh", "/usr/bin/ssh") == {"ssh"}


def test_media_is_parallel_requirement():
    result = DemandObserver(cpu_count=4).observe(sample(media_playing=True))
    assert result["media_continuity"] == 1.0
    assert "MEDIA" in result["region"]
