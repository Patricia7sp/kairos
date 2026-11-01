"""O lifespan real drena seus recursos antes de propagar cancelamento."""

import asyncio

import pytest
from fastapi import FastAPI

from kairos_observability.service_events import read_service_events
from kairos_web import server


class ClosingService:
    def __init__(self, *, blocked=False, failure=None):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        if not blocked:
            self.release.set()
        self.failure = failure
        self.closed = False

    async def aclose(self):
        self.entered.set()
        await self.release.wait()
        if self.failure is not None:
            raise self.failure
        self.closed = True


def application_with_service(home, monkeypatch, service, *, injected=False):
    application = FastAPI()
    application.state.kairos_home = home
    if injected:
        application.state.interaction_service = service
    else:
        monkeypatch.setattr(server, "build_interaction_service", lambda home: service)
    return application


@pytest.mark.parametrize("injected", [False, True])
def test_shutdown_preserves_cancel_after_scheduler_completion(tmp_path, monkeypatch, injected):
    async def run():
        service = ClosingService()
        application = application_with_service(tmp_path, monkeypatch, service, injected=injected)
        lifespan = server._lifespan(application)
        await lifespan.__aenter__()
        scheduler = application.state.cron_task
        closer = None
        scheduler.add_done_callback(lambda completed: closer.cancel())
        closer = asyncio.create_task(lifespan.__aexit__(None, None, None))
        with pytest.raises(asyncio.CancelledError):
            await closer
        assert scheduler.done()
        assert service.closed is (not injected)
        assert not hasattr(application.state, "cron_task")
        assert not hasattr(application.state, "cron_scheduler")
        assert hasattr(application.state, "interaction_service") is injected
        codes = {event["code"] for event in read_service_events(tmp_path)["events"]}
        assert {"web.started", "web.stopped"} <= codes

    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, RuntimeError("synthetic service close failure")])
def test_shutdown_drains_service_despite_repeated_cancel(tmp_path, monkeypatch, failure):
    async def run():
        service = ClosingService(blocked=True, failure=failure)
        application = application_with_service(tmp_path, monkeypatch, service)
        lifespan = server._lifespan(application)
        await lifespan.__aenter__()
        closer = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await service.entered.wait()
            closer.cancel()
            await asyncio.sleep(0)
            closer.cancel()
            await asyncio.sleep(0)
            assert not closer.done()
            service.release.set()
            if failure is None:
                with pytest.raises(asyncio.CancelledError):
                    await closer
                assert service.closed
            else:
                with pytest.raises(RuntimeError) as caught:
                    await closer
                assert caught.value is failure
                assert isinstance(caught.value.__cause__, asyncio.CancelledError)
            assert not hasattr(application.state, "cron_task")
            assert not hasattr(application.state, "interaction_service")
        finally:
            service.release.set()
            await asyncio.gather(closer, return_exceptions=True)

    asyncio.run(run())
