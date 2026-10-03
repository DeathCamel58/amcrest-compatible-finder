import threading
import time

from util.scheduler import HostScheduler


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}
        self.max_running = {}
        self.order = []
        self.done = []

    def task(self, host, task_id, delay=0.02):
        with self.lock:
            self.order.append(host)
            self.running[host] = self.running.get(host, 0) + 1
            self.max_running[host] = max(self.max_running.get(host, 0), self.running[host])
        time.sleep(delay)
        with self.lock:
            self.running[host] -= 1
            self.done.append((host, task_id))


def test_spreads_across_hosts_and_runs_everything():
    recorder = Recorder()
    scheduler = HostScheduler(4)
    hosts = ["a.com", "b.com", "c.com", "d.com"]
    for host in hosts:
        for i in range(3):
            scheduler.add(host, recorder.task, host, i)
    assert scheduler.run() == []
    # The first four tasks started go to four different hosts
    assert sorted(recorder.order[:4]) == hosts
    assert sorted(recorder.done) == sorted((host, i) for host in hosts for i in range(3))


def test_biggest_host_starts_first():
    recorder = Recorder()
    scheduler = HostScheduler(1)
    scheduler.add("small.com", recorder.task, "small.com", 0, 0)
    for i in range(3):
        scheduler.add("big.com", recorder.task, "big.com", i, 0)
    scheduler.run()
    assert recorder.order[0] == "big.com"


def test_caps_honoured():
    recorder = Recorder()
    scheduler = HostScheduler(4, host_caps={"example.com": 1, "archive.org": 2})
    for i in range(5):
        scheduler.add("dl.example.com", recorder.task, "dl.example.com", i)
        scheduler.add("web.archive.org", recorder.task, "web.archive.org", i)
    for i in range(2):
        scheduler.add("other.net", recorder.task, "other.net", i)
    assert scheduler.run() == []
    assert recorder.max_running["dl.example.com"] == 1
    assert recorder.max_running["web.archive.org"] <= 2
    assert len(recorder.done) == 12


def test_uncapped_host_uses_spare_workers():
    recorder = Recorder()
    scheduler = HostScheduler(3)
    for i in range(6):
        scheduler.add("only.com", recorder.task, "only.com", i, 0.05)
    scheduler.run()
    assert recorder.max_running["only.com"] > 1
    assert len(recorder.done) == 6


def test_cap_matching():
    scheduler = HostScheduler(8, host_caps={"example.com": 2})
    assert scheduler.cap("example.com") == 2
    assert scheduler.cap("dl.example.com") == 2
    assert scheduler.cap("badexample.com") == 8


def test_failing_task_recorded_and_others_run():
    recorder = Recorder()
    scheduler = HostScheduler(2)

    def boom():
        raise ValueError("boom")

    scheduler.add("a.com", boom)
    for i in range(3):
        scheduler.add("a.com", recorder.task, "a.com", i, 0)
    errors = scheduler.run()
    assert len(errors) == 1 and errors[0][0] == "a.com" and isinstance(errors[0][1], ValueError)
    assert len(recorder.done) == 3


def test_no_tasks():
    assert HostScheduler(3).run() == []
