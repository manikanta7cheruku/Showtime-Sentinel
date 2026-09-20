from app.models import AvailabilityState, NormalizedResult, NotificationKind


def test_create_read_list(repos, spec):
    watch = repos["watches"].create(spec)
    assert watch.id is not None
    assert watch.current_state is AvailabilityState.UNKNOWN
    fetched = repos["watches"].get(watch.id)
    assert fetched.movie_name == spec.movie_name
    assert fetched.exact_seats == spec.exact_seats
    assert len(repos["watches"].list_watches()) == 1


def test_enable_disable_and_enabled_only_filter(repos, spec):
    watch = repos["watches"].create(spec)
    assert repos["watches"].set_enabled(watch.id, False) is True
    assert repos["watches"].list_watches(enabled_only=True) == []
    repos["watches"].set_enabled(watch.id, True)
    assert len(repos["watches"].list_watches(enabled_only=True)) == 1


def test_delete_cascades_snapshots(repos, spec):
    watch = repos["watches"].create(spec)
    repos["snapshots"].add(watch.id, NormalizedResult(
        state=AvailabilityState.BOOKING_OPEN, source_name="fake", availability_hash="h"))
    assert repos["snapshots"].count_for(watch.id) == 1
    assert repos["watches"].delete(watch.id) is True
    assert repos["snapshots"].count_for(watch.id) == 0
    assert repos["watches"].get(watch.id) is None


def test_record_check_success_then_failure(repos, spec):
    watch = repos["watches"].create(spec)
    repos["watches"].record_check(watch.id, NormalizedResult(
        state=AvailabilityState.SEATS_AVAILABLE, source_name="fake",
        availability_hash="abc"), notified=True)
    after = repos["watches"].get(watch.id)
    assert after.current_state is AvailabilityState.SEATS_AVAILABLE
    assert after.current_hash == "abc"
    assert after.notification_count == 1
    assert after.last_success_at is not None
    assert after.consecutive_failures == 0

    repos["watches"].record_check(watch.id, NormalizedResult(
        state=AvailabilityState.ERROR, source_name="fake", error="boom"), notified=False)
    failed = repos["watches"].get(watch.id)
    assert failed.consecutive_failures == 1
    assert failed.last_error == "boom"
    # A failure must NOT clobber the last known good timestamp or hash.
    assert failed.last_success_at is not None
    assert failed.current_hash == "abc"


def test_dedupe_lookup(repos, spec):
    watch = repos["watches"].create(spec)
    key = f"{watch.id}|A->B|hash"
    assert repos["notifications"].exists_within(key, 0) is False
    repos["notifications"].add(watch_id=watch.id, kind=NotificationKind.STATE_CHANGE,
                               dedupe_key=key, channel="console", delivered=True)
    assert repos["notifications"].exists_within(key, 0) is True
    assert repos["notifications"].count_for(watch.id) == 1


def test_sql_injection_attempt_is_stored_as_data(repos, spec):
    nasty = spec.model_copy(update={"movie_name": "Robert'); DROP TABLE watches;--"})
    watch = repos["watches"].create(nasty)
    assert repos["watches"].get(watch.id).movie_name == nasty.movie_name
    assert len(repos["watches"].list_watches()) == 1   # table still exists
