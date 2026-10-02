import threading
from collections import deque


class HostScheduler:
    """Run tasks with a fixed number of workers, spreading them across hosts.

    Each free worker takes its next task from the host with the fewest tasks running, so many different servers are
    downloaded from at once. When fewer hosts have work left than there are workers, hosts get a second (third, ...)
    worker each to keep the pool busy, up to any per-host cap.
    """

    def __init__(self, workers, host_caps=None):
        self.workers = workers
        self.host_caps = host_caps or {}
        self.pending = {}
        self.running = {}
        self.condition = threading.Condition()
        self.errors = []

    def add(self, host, task, *args):
        self.pending.setdefault(host, deque()).append((task, args))

    def cap(self, host):
        for suffix, limit in self.host_caps.items():
            if host == suffix or host.endswith("." + suffix):
                return limit
        return self.workers

    def next_task(self):
        """Wait for a task this worker may run. Returns (host, task, args), or None when everything is done."""
        with self.condition:
            while True:
                hosts = [host for host, tasks in self.pending.items() if tasks]
                if not hosts:
                    return None
                available = [host for host in hosts if self.running.get(host, 0) < self.cap(host)]
                if available:
                    # Fewest running first; among equals, the host with the most work left, so big servers start early
                    host = min(available, key=lambda h: (self.running.get(h, 0), -len(self.pending[h])))
                    self.running[host] = self.running.get(host, 0) + 1
                    task, args = self.pending[host].popleft()
                    return host, task, args
                # Every host with work is at its cap; wait for one of their tasks to finish
                self.condition.wait()

    def worker(self):
        while True:
            item = self.next_task()
            if item is None:
                return
            host, task, args = item
            try:
                task(*args)
            except Exception as err:
                self.errors.append((host, err))
                print(f'\tTask for {host} failed: {err!r}')
            finally:
                with self.condition:
                    self.running[host] -= 1
                    self.condition.notify_all()

    def run(self):
        threads = [threading.Thread(target=self.worker, daemon=True) for _ in range(self.workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return self.errors
