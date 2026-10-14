from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app
from app.repository import SnapshotUnavailableError


def test_storage_outage_is_503_not_a_missing_archive_or_safe_hold():
    with patch('app.main.repository.league', side_effect=SnapshotUnavailableError('storage timed out')):
        client = TestClient(app)
        response = client.get('/v1/leagues/58005/compact?gw=6')
    assert response.status_code == 503
    assert response.json() == {'detail': 'snapshot_storage_unavailable'}
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['retry-after'] == '2'
