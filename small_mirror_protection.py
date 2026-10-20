"""Content-cover timing, independent of per-visit statistics and face storage."""
from math import ceil


class ProtectionPolicy:
    def __init__(self):
        self.cooldown_until = 0.
        self.reset()

    def reset(self):
        # Configuration changes rearm acquisition without cancelling a snooze.
        self.serial = -1
        self.last_at = None
        self.since = None
        self.signal = None
        self.hits = 0
        self.latched = False

    def release(self, now, seconds=30):
        self.cooldown_until = now + max(0, seconds)
        self.reset()

    def remaining(self, now):
        return max(0, ceil(self.cooldown_until - now))

    def update(self, now, serial, signal, active, automatic, attentive=True):
        """True=looking, False=confirmed away/absent, None=unknown/failed.

        Only distinct frames with gaps <= 1.25 s sustain a decision. A stalled
        image or uncertain pose can never establish absence and uncover content.
        """
        if signal is None:
            self.since = self.last_at = self.signal = None
            self.hits = 0
            return None
        if serial <= self.serial:
            return None
        self.serial = serial
        if self.remaining(now):
            self.since = self.last_at = self.signal = None
            self.hits = 0
            return None
        if self.last_at is None or now-self.last_at > 1.25 or signal != self.signal:
            self.since, self.hits = now, 0
        self.last_at, self.signal = now, signal
        self.hits += 1
        if not signal:
            if self.hits >= 3 and now-self.since >= 2:
                self.latched = False
                if active and automatic:
                    self.reset()
                    return 'uncover'
        elif not active and not self.latched:
            if self.hits >= (3 if attentive else 2) and now-self.since >= (1 if attentive else .3):
                self.latched = True
                return 'cover'
        return None
