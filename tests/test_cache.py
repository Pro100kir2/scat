import asyncio
import json
import time

import pytest

from app.services import scan_service as scan_service_module
from app.services.scan_service import ALL
from app.services.scanner import ScanQuery
from tests.conftest import FakeScanner


def run(coro):
    return asyncio.run(coro)


def test_cache_miss_triggers_scan_and_stores_result(make_service):
    async def scenario():
        service = make_service()
        assert service.cache_state() == "empty"
        assert service.request_scan() == "started"
        await service.wait()
        return service

    service = run(scenario())
    assert service.scanner.runs == 1
    assert service.cache_state() == "fresh"
    meta = service.cache_meta()
    assert meta["count"] == 1 and meta["working"] == 25 and meta["tested"] == 60
    assert meta["scanner_version"] == "test"
    assert meta["created_at"] < meta["expires_at"]


def test_cache_hit_does_not_scan(make_service):
    async def scenario():
        service = make_service()
        service.request_scan()
        await service.wait()
        outcomes = [service.request_scan() for _ in range(5)]
        return service, outcomes

    service, outcomes = run(scenario())
    assert outcomes == ["cached"] * 5
    assert service.scanner.runs == 1
    assert service.stats["cache_hits"] == 5


def test_concurrent_requests_share_one_scan(make_service):
    async def scenario():
        service = make_service(FakeScanner(delay=0.2))

        async def visitor():
            outcome = service.request_scan()
            await service.wait()
            return outcome

        outcomes = await asyncio.gather(*(visitor() for _ in range(100)))
        return service, outcomes

    service, outcomes = run(scenario())
    assert service.scanner.runs == 1
    assert outcomes.count("started") == 1
    assert outcomes.count("joined") == 99
    assert service.cache_state() == "fresh"


def test_expired_cache_is_served_as_stale_and_rescanned(make_service):
    async def scenario():
        service = make_service()
        service._slot(ALL).cache.save(await FakeScanner().run_scan(), now=time.time() - 2 * 86400)
        state_before = service.cache_state()
        outcome = service.request_scan()
        await service.wait()
        return service, state_before, outcome

    service, state_before, outcome = run(scenario())
    assert state_before == "stale"
    assert outcome == "started"
    assert service.cache_state() == "fresh"


def test_cache_from_other_scanner_version_is_ignored(make_service, tmp_path):
    async def scenario():
        old = make_service(version="1.0")
        old.request_scan()
        await old.wait()
        return make_service(version="2.0")

    new = run(scenario())
    assert new.entry() is None
    assert new.cache_state() == "empty"


def test_corrupt_cache_file_is_ignored(make_service, tmp_path):
    (tmp_path / "scan_all.json").write_text("{not json")
    assert make_service().cache_state() == "empty"


def test_empty_result_gets_short_ttl(make_service):
    async def scenario():
        service = make_service(FakeScanner(nodes=[]))
        service.request_scan()
        await service.wait()
        return service

    entry = run(scenario()).entry()
    ttl = entry["expires_ts"] - entry["created_ts"]
    assert ttl == scan_service_module.EMPTY_RESULT_TTL_SECONDS


def test_failed_scan_starts_cooldown(make_service):
    async def scenario():
        service = make_service(FakeScanner(fail=True))
        service.request_scan()
        await service.wait()
        return service, service.request_scan()

    service, outcome = run(scenario())
    assert outcome == "cooldown"
    assert service.scanner.runs == 1
    assert service.status()["last_scan_failed"] is True
    assert service.cache_state() == "empty"


def test_force_bypasses_fresh_cache(make_service):
    async def scenario():
        service = make_service()
        service.request_scan()
        await service.wait()
        outcome = service.request_scan(force=True)
        await service.wait()
        return service, outcome

    service, outcome = run(scenario())
    assert outcome == "started"
    assert service.scanner.runs == 2


def test_cache_written_by_another_process_is_picked_up(make_service):
    async def scenario():
        reader = make_service()
        assert reader.cache_state() == "empty"
        writer = make_service()
        writer.request_scan()
        await writer.wait()
        return reader

    reader = run(scenario())
    assert reader.cache_state() == "fresh"
    assert reader.request_scan() == "cached"
    assert reader.scanner.runs == 0


def test_cache_file_is_valid_json_with_metadata(make_service, tmp_path):
    async def scenario():
        service = make_service()
        service.request_scan()
        await service.wait()

    run(scenario())
    data = json.loads((tmp_path / "scan_all.json").read_text())
    for key in ("schema", "scanner_version", "created_at", "expires_at", "count", "top", "working"):
        assert key in data


def test_each_country_has_its_own_cache_and_single_flight(make_service, tmp_path):
    async def scenario():
        service = make_service(FakeScanner(delay=0.1))
        de, fr = ScanQuery("DE"), ScanQuery("FR")
        outcomes = [service.request_scan(de) for _ in range(20)] + [service.request_scan(fr)]
        await service.wait(de)
        await service.wait(fr)
        return service, outcomes

    service, outcomes = run(scenario())
    assert outcomes.count("started") == 2  # one per country, not 21
    assert [q.country for q in service.scanner.queries] == ["DE", "FR"]
    assert service.cache_state(ScanQuery("DE")) == "fresh"
    assert service.cache_state(ALL) == "empty"
    assert (tmp_path / "scan_DE.json").exists() and (tmp_path / "scan_FR.json").exists()


def test_country_scan_queue_is_bounded(make_service):
    async def scenario():
        service = make_service(FakeScanner(delay=0.2))  # max_queued=2 in the fixture
        outcomes = [service.request_scan(ScanQuery(c)) for c in ("DE", "FR", "NL")]
        for c in ("DE", "FR"):
            await service.wait(ScanQuery(c))
        return outcomes

    assert run(scenario()) == ["started", "started", "busy"]
