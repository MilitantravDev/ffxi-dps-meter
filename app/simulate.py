"""Sends fake DPSLink traffic to the app so you can try the UI without the game.
Two "characters" in the same party report the same actions (tests de-duplication).

    python simulate.py [seconds]
"""
import json
import random
import socket
import sys
import time

PORT = 24250
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

PARTY = [
    (1, "Alpha", "me", None, ["Savage Blade", "Knights of Round"]),
    (2, "Bravo", "party", None, ["Rudra's Storm", "Evisceration"]),
    (3, "Charlie", "party", None, []),
    (4, "Shantotto", "trust", None, []),
    (5, "Ifrit", "pet", "Charlie", ["Flaming Crush"]),
]
SPELLS = ["Fire V", "Thunder V", "Blizzard V"]
SOURCES = ["Alpha", "Bravo"]


def send(pkt, src):
    pkt = dict(pkt, src=src)
    sock.sendto(json.dumps(pkt).encode(), ("127.0.0.1", PORT))


def broadcast(pkt):
    for src in SOURCES:
        send(pkt, src)


def heartbeat():
    party = [{"id": i, "n": n, "k": k, "o": o} for i, n, k, o, _ in PARTY]
    send({"t": "hb", "id": 1, "job": "RDM99/NIN49", "zone": "Sortie", "party": party}, "Alpha")
    send({"t": "hb", "id": 2, "job": "THF99/WAR49", "zone": "Sortie", "party": party}, "Bravo")


def action(mob_id, mob):
    aid, name, kind, owner, wss = random.choice(PARTY)
    roll = random.random()
    hits = []
    if name == "Charlie" or (name == "Shantotto" and roll < 0.7):
        sp = random.choice(SPELLS)
        dmg = random.randint(4000, 12000)
        hits.append({"mid": mob_id, "mn": mob, "c": "spell", "ab": sp, "d": dmg})
        cat = 4
    elif wss and roll < 0.25:
        ws = random.choice(wss)
        dmg = random.randint(8000, 40000)
        hits.append({"mid": mob_id, "mn": mob, "c": "ws" if kind != "pet" else "ability", "ab": ws, "d": dmg})
        if random.random() < 0.4:
            hits.append({"mid": mob_id, "mn": mob, "c": "sc", "ab": "SC: " + ws, "d": random.randint(5000, 20000)})
        cat = 3
    else:
        cat = 1
        for _ in range(random.randint(1, 4)):
            r = random.random()
            if r < 0.1:
                hits.append({"mid": mob_id, "mn": mob, "c": "melee", "ab": "Melee", "f": "miss"})
            elif r < 0.3:
                hits.append({"mid": mob_id, "mn": mob, "c": "melee", "ab": "Melee", "d": random.randint(1500, 3000), "f": "crit"})
            else:
                hits.append({"mid": mob_id, "mn": mob, "c": "melee", "ab": "Melee", "d": random.randint(600, 1500)})
    broadcast({"t": "act", "aid": aid, "an": name, "ak": kind, "ao": owner, "cat": cat, "p": 0, "hits": hits})


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    mobs = ["Apex Bat", "Ghatjot", "Leshonn", "Gartell"]
    end = time.time() + seconds
    last_hb = 0
    while time.time() < end:
        mob = random.choice(mobs)
        fight_end = time.time() + random.uniform(8, 20)
        print("fighting", mob)
        while time.time() < fight_end and time.time() < end:
            if time.time() - last_hb > 2:
                heartbeat()
                last_hb = time.time()
            action(random.randint(1000, 1003), mob)
            time.sleep(random.uniform(0.1, 0.4))
        print("  idle")
        idle_end = time.time() + 10
        while time.time() < idle_end and time.time() < end:
            heartbeat()
            time.sleep(2)


if __name__ == "__main__":
    main()
