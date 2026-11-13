"""DPS engine: receives action packets from DPSLink characters, de-duplicates them,
splits them into combats and aggregates with (retroactive) view filters.
No UI code here so it can be tested on its own."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

CAT_ORDER = ["melee", "ranged", "ws", "spell", "ability", "sc", "addeff", "spikes"]
CAT_LABEL = {
    "melee": "Melee", "ranged": "Ranged", "ws": "Weapon Skill", "spell": "Magic",
    "ability": "Ability", "sc": "Skillchain", "addeff": "Add. Effect", "spikes": "Spikes",
}

DEDUPE_WINDOW = 1.5      # seconds: same action from a different character within this = duplicate
SOURCE_STALE = 6.0       # seconds without heartbeat before a character shows as disconnected


@dataclass
class Stat:
    total: int = 0
    hits: int = 0
    misses: int = 0
    crits: int = 0
    max: int = 0

    def add(self, dmg, flag):
        if dmg is None:
            self.misses += 1
            return
        self.total += dmg
        self.hits += 1
        if dmg > self.max:
            self.max = dmg
        if flag == "crit":
            self.crits += 1

    def merge(self, o: "Stat"):
        self.total += o.total
        self.hits += o.hits
        self.misses += o.misses
        self.crits += o.crits
        self.max = max(self.max, o.max)

    @property
    def avg(self):
        return self.total / self.hits if self.hits else 0

    @property
    def acc(self):
        n = self.hits + self.misses
        return 100 * self.hits / n if n else 0

    @property
    def crit_rate(self):
        return 100 * self.crits / self.hits if self.hits else 0


@dataclass
class Actor:
    name: str
    kind: str
    owner: str | None
    # by_mob[mob][cat] -> (Stat, {ability: Stat})
    by_mob: dict = field(default_factory=dict)
    seen_by: set = field(default_factory=set)


@dataclass
class Combat:
    id: int
    start: float
    last: float
    mobs: dict = field(default_factory=dict)       # mob name -> damage
    actors: dict = field(default_factory=dict)     # key -> Actor
    sources: set = field(default_factory=set)
    active: bool = True

    def duration(self, now=None):
        end = (now or time.time()) if self.active else self.last
        return max(1.0, end - self.start)

    def primary_mob(self, mob_ok=lambda m: True):
        best = max(((d, m) for m, d in self.mobs.items() if mob_ok(m)), default=None)
        return best[1] if best else "-"


@dataclass
class Filters:
    mob: str = ""                 # substring, case-insensitive
    cats: set | None = None       # allowed categories (None = all)
    source: str | None = None     # only actions reported by this character
    show_pets: bool = True
    show_trusts: bool = True
    show_alliance: bool = True
    merge_pets: bool = True

    def mob_ok(self, mob):
        return not self.mob or self.mob.lower() in mob.lower()

    def cat_ok(self, cat):
        return self.cats is None or cat in self.cats

    def kind_ok(self, kind):
        return {"pet": self.show_pets, "trust": self.show_trusts,
                "alliance": self.show_alliance}.get(kind, True)


@dataclass
class Row:
    name: str
    kind: str
    damage: int = 0


@dataclass
class Source:
    name: str
    last_seen: float = 0
    zone: str = ""
    job: str = ""
    packets: int = 0
    dupes: int = 0

    def online(self, now=None):
        return (now or time.time()) - self.last_seen < SOURCE_STALE


class Engine:
    def __init__(self, timeout=8.0, max_combats=50):
        self.timeout = timeout
        self.max_combats = max_combats
        self.combats: list[Combat] = []
        self.current: Combat | None = None
        self.next_id = 1
        self.sources: dict[str, Source] = {}
        self._recent: dict[str, list] = {}   # dedupe key -> [[src, time, matched], ...]
        self.version = 0                     # bumps on every data change (UI redraw hint)

    # ------------------------------------------------------------------ input
    def feed_raw(self, data: bytes, now=None):
        try:
            pkt = json.loads(data.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(pkt, dict):
            self.feed(pkt, now)

    def feed(self, pkt: dict, now=None):
        now = now or time.time()
        src_name = pkt.get("src") or "?"
        src = self.sources.get(src_name)
        if not src:
            src = self.sources[src_name] = Source(src_name)
        src.last_seen = now
        src.packets += 1
        t = pkt.get("t")
        if t == "hb":
            src.zone = pkt.get("zone") or ""
            src.job = pkt.get("job") or ""
        elif t == "bye":
            src.last_seen = 0
        elif t == "reset":
            self.reset()
        elif t == "act":
            if self._is_duplicate(pkt, src_name, now):
                src.dupes += 1
                return
            self._record(pkt, src_name, now)

    def _is_duplicate(self, pkt, src, now):
        key = json.dumps([pkt.get("aid"), pkt.get("cat"), pkt.get("p"),
                          [(h.get("mid"), h.get("c"), h.get("ab"), h.get("d"), h.get("f"))
                           for h in pkt.get("hits", [])]])
        # drop stale entries occasionally
        if len(self._recent) > 2000:
            self._recent = {k: [e for e in v if now - e[1] < DEDUPE_WINDOW]
                            for k, v in self._recent.items()}
            self._recent = {k: v for k, v in self._recent.items() if v}
        entries = [e for e in self._recent.get(key, []) if now - e[1] < DEDUPE_WINDOW]
        for e in entries:
            if e[0] != src and src not in e[2]:
                e[2].add(src)        # this source's copy of that action is now accounted for
                self._recent[key] = entries
                return True
        entries.append([src, now, set()])
        self._recent[key] = entries
        return False

    def _record(self, pkt, src, now):
        if self.current is None:
            self.current = Combat(self.next_id, now, now)
            self.next_id += 1
            self.combats.append(self.current)
            del self.combats[:-self.max_combats]
        c = self.current
        c.sources.add(src)
        name, kind, owner = pkt.get("an") or "?", pkt.get("ak") or "party", pkt.get("ao")
        key = f"{name}|{owner}" if owner else name
        actor = c.actors.get(key)
        if not actor:
            actor = c.actors[key] = Actor(name, kind, owner)
        if kind != "pet":
            actor.kind = kind
        actor.seen_by.add(src)
        for h in pkt.get("hits", []):
            mob, cat = h.get("mn") or "?", h.get("c") or "melee"
            ability = h.get("ab") or CAT_LABEL.get(cat, cat)
            dmg = h.get("d")
            if h.get("f") == "miss":
                dmg = None
            elif not isinstance(dmg, (int, float)):
                continue
            cats = actor.by_mob.setdefault(mob, {})
            if cat not in cats:
                cats[cat] = (Stat(), {})
            stat, abil = cats[cat]
            stat.add(dmg, h.get("f"))
            abil.setdefault(ability, Stat()).add(dmg, h.get("f"))
            if dmg is not None:
                c.mobs[mob] = c.mobs.get(mob, 0) + dmg
                c.last = now
        self.version += 1

    # ------------------------------------------------------------- lifecycle
    def tick(self, now=None):
        now = now or time.time()
        if self.current and now - self.current.last > self.timeout:
            self.end_combat()

    def end_combat(self):
        if self.current:
            self.current.active = False
            self.current = None
            self.version += 1

    def reset(self):
        self.combats.clear()
        self.current = None
        self.next_id = 1
        self._recent.clear()
        self.version += 1

    # ------------------------------------------------------------ selection
    def select(self, target) -> list[Combat]:
        """target: 'current' | 'all' | combat id"""
        if target == "all":
            return list(self.combats)
        if target == "current":
            c = self.current or (self.combats[-1] if self.combats else None)
            return [c] if c else []
        return [c for c in self.combats if c.id == target]

    # ----------------------------------------------------------- aggregation
    def _actor_ok(self, actor: Actor, f: Filters):
        if not f.kind_ok(actor.kind):
            return False
        return f.source is None or f.source in actor.seen_by

    def _display_name(self, actor: Actor, f: Filters):
        if actor.kind == "pet" and actor.owner:
            return actor.owner if f.merge_pets else f"{actor.name} ({actor.owner})"
        return actor.name

    def aggregate(self, combats, f: Filters, now=None):
        rows: dict[str, Row] = {}
        total = 0
        duration = sum(c.duration(now) for c in combats) if combats else 1.0
        for c in combats:
            for actor in c.actors.values():
                if not self._actor_ok(actor, f):
                    continue
                name = self._display_name(actor, f)
                row = rows.get(name)
                if not row:
                    row = rows[name] = Row(name, actor.kind)
                if actor.kind != "pet":
                    row.kind = actor.kind
                for mob, cats in actor.by_mob.items():
                    if not f.mob_ok(mob):
                        continue
                    for cat, (stat, _) in cats.items():
                        if f.cat_ok(cat):
                            row.damage += stat.total
                            total += stat.total
        out = sorted((r for r in rows.values() if r.damage > 0), key=lambda r: -r.damage)
        return out, total, duration

    def breakdown(self, combats, who: str, f: Filters):
        """Returns {cat: (Stat, {ability: Stat})}, total for one display row."""
        cats: dict[str, tuple[Stat, dict]] = {}
        total = 0
        for c in combats:
            for actor in c.actors.values():
                if not self._actor_ok(actor, f) or self._display_name(actor, f) != who:
                    continue
                prefix = f"{actor.name}: " if actor.kind == "pet" and f.merge_pets else ""
                for mob, mc in actor.by_mob.items():
                    if not f.mob_ok(mob):
                        continue
                    for cat, (stat, abil) in mc.items():
                        if not f.cat_ok(cat):
                            continue
                        agg = cats.setdefault(cat, (Stat(), {}))
                        agg[0].merge(stat)
                        total += stat.total
                        for name, s in abil.items():
                            agg[1].setdefault(prefix + name, Stat()).merge(s)
        return cats, total

    def mob_breakdown(self, combats, f: Filters):
        """Damage dealt to each mob (respecting filters)."""
        mobs: dict[str, int] = {}
        for c in combats:
            for actor in c.actors.values():
                if not self._actor_ok(actor, f):
                    continue
                for mob, mc in actor.by_mob.items():
                    if f.mob_ok(mob):
                        mobs[mob] = mobs.get(mob, 0) + sum(s.total for cat, (s, _) in mc.items() if f.cat_ok(cat))
        return sorted(mobs.items(), key=lambda kv: -kv[1])

    def all_sources(self):
        return sorted(self.sources.values(), key=lambda s: s.name)


def fmt_num(n):
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e4:
        return f"{n / 1e3:.1f}k"
    return str(int(n))


def fmt_time(s):
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"
