import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/integration/deployment-smoke.sh'


def run_smoke(tmp_path, **changes):
    env = os.environ.copy()
    env.update(
        APP_PERSIST_ROOT=str(tmp_path / 'data'),
        REQUIRE_PERSISTENT_STORAGE='1',
        SMOKE_STORAGE_STATUS='ready',
        SMOKE_POSTGRES_STATUS='ready',
        SMOKE_POSTGRES_PORT_STATUS='ready',
        SMOKE_PLANDEX_STATUS='ready',
        SMOKE_PLANDEX_PORT_STATUS='ready',
        SMOKE_TOKENIZER_STATUS='ready',
        SMOKE_PLANDEX_AUTH_STATUS='ready',
        SMOKE_OPEN_WEBUI_STATUS='ready',
        SMOKE_PUBLIC_GUARD_STATUS='ready',
        SMOKE_GRAPHIFY_STATUS='ready',
        SMOKE_NEEDLE_STATUS='ready',
        SPARK_API_KEY='',
        SPARK_BASE_URL='',
    )
    env.update(changes)
    report = tmp_path / 'readiness.json'
    result = subprocess.run([str(SCRIPT), str(report)], env=env, text=True, capture_output=True)
    return result, json.loads(report.read_text())


def test_no_spark_credentials_keeps_baseline_ready(tmp_path):
    result, report = run_smoke(tmp_path)
    assert result.returncode == 0
    assert report['deployment_ready'] is True
    assert report['degraded']['spark'] == 'not_configured'
    assert report['degraded']['headroom'] == 'not_configured'


def test_configured_spark_dead_is_degraded(tmp_path):
    result, report = run_smoke(
        tmp_path, SPARK_API_KEY='configured', SPARK_BASE_URL='https://spark.invalid/v1', SMOKE_SPARK_STATUS='unavailable'
    )
    assert result.returncode == 0
    assert report['deployment_ready'] is True
    assert report['degraded']['spark'] == 'unavailable'


def test_graphify_failure_is_degraded(tmp_path):
    result, report = run_smoke(tmp_path, SMOKE_GRAPHIFY_STATUS='failed')
    assert result.returncode == 0
    assert report['deployment_ready'] is True
    assert report['degraded']['graphify'] == 'failed'


def test_needle_failure_is_degraded(tmp_path):
    result, report = run_smoke(tmp_path, SMOKE_NEEDLE_STATUS='failed')
    assert result.returncode == 0
    assert report['deployment_ready'] is True
    assert report['degraded']['needle'] == 'failed'


def test_critical_failures_make_deployment_not_ready(tmp_path):
    for variable in ('SMOKE_PLANDEX_STATUS', 'SMOKE_POSTGRES_STATUS', 'SMOKE_TOKENIZER_STATUS'):
        result, report = run_smoke(tmp_path, **{variable: 'failed'})
        assert result.returncode != 0
        assert report['deployment_ready'] is False


def test_unexpected_public_listener_is_fatal(tmp_path):
    result, report = run_smoke(tmp_path, SMOKE_PUBLIC_GUARD_STATUS='failed')
    assert result.returncode != 0
    assert report['deployment_ready'] is False
    assert report['critical']['public_listener_guard'] == 'failed'
