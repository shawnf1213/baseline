"""Seed the FORWARD calibration log with the first clean out-of-sample batch — the
07-25 board (post-FS-changes deploy), graded against final Sofascore stats. This is the
lookahead-free sample: every projection was made pre-match, graded after. Going forward,
each night's board is appended as pending and graded the next day, accumulating a real
FS/prop calibration sample with ZERO lookahead and no stat-pipeline surgery."""
import json, os

LOG = os.path.join(os.path.dirname(__file__), "forward_calibration.json")
CONFIG = "2026.07.25-fs-breakability+faceace+gamespread"

# (tour, player, opponent, prop, line, lean, proj, actual, result)
BATCH_0725 = [
 ("WTA","Barbora Krejcikova","Lilli Tagger","Fantasy Score",22.0,"UNDER",15.4,3.0,"W"),
 ("WTA","Daria Kasatkina","Mariam Bolkvadze","Fantasy Score",20.0,"UNDER",14.5,14.0,"W"),
 ("WTA","Polina Kudermetova","Mei Yamaguchi","Fantasy Score",19.5,"UNDER",16.1,15.5,"W"),
 ("ATP","Luciano Darderi","Alexander Blockx","Break Points Won",3.5,"UNDER",2.0,6.0,"L"),
 ("WTA","Polina Iatcenko","Julieta Pareja","Double Faults",4.5,"UNDER",3.1,6.0,"L"),
 ("WTA","Fiona Ferro","Lucia Bronzetti","Total Games",21.5,"OVER",23.9,17.0,"L"),
 ("WTA","Carolyn Ansari","Rebecca Sramkova","Double Faults",4.5,"UNDER",3.4,4.0,"W"),
 ("WTA","Julieta Pareja","Polina Iatcenko","Break Points Won",4.5,"UNDER",3.1,4.0,"W"),
 ("ATP","Alexander Bublik","Quentin Halys","Fantasy Score",21.0,"UNDER",15.6,3.5,"W"),
 ("ATP","Hugo Gaston","Luca Van Assche","Aces",2.5,"UNDER",1.7,0.0,"W"),
 ("WTA","Lilli Tagger","Barbora Krejcikova","Player Total Games Won",7.5,"OVER",10.2,19.0,"W"),
 ("ATP","Billy Harris","Abdullah Shelbayh","Total Games",21.5,"OVER",23.5,23.0,"W"),
 ("WTA","Julieta Pareja","Polina Iatcenko","Total Games",21.5,"OVER",23.4,18.0,"L"),
 ("WTA","Katie Swan","Lea Ma","Fantasy Score",16.5,"UNDER",12.6,None,"VOID"),
 ("WTA","Varvara Lepchenko","Clervie Ngounoue","Break Points Won",4.5,"UNDER",4.0,5.0,"L"),
 ("WTA","Tereza Valentova","Daria Snigur","Fantasy Score",15.5,"UNDER",11.4,-2.0,"W"),
 ("WTA","Anna Bondar","Elina Avanesyan","Fantasy Score",16.0,"OVER",21.1,13.5,"L"),
 ("ATP","Luciano Darderi","Alexander Blockx","Fantasy Score",16.5,"UNDER",13.8,7.5,"W"),
 ("WTA","Rebecca Sramkova","Carolyn Ansari","Fantasy Score",20.5,"UNDER",19.0,23.0,"L"),
 ("ATP","Quentin Halys","Alexander Bublik","Aces",10.5,"UNDER",9.9,17.0,"L"),
]


def rec(date, config, r):
    tour, p, o, pt, line, lean, proj, actual, res = r
    return {"date": date, "config": config, "tour": tour, "player": p, "opponent": o,
            "prop_type": pt, "line": line, "lean": lean, "projection": proj,
            "actual": actual, "result": res}


def main():
    if os.path.exists(LOG):
        doc = json.load(open(LOG))
    else:
        doc = {"meta": {"purpose": "forward (out-of-sample) calibration; graded post-match, no lookahead"},
               "plays": []}
    have = {(x["date"], x["player"], x["prop_type"], x["line"]) for x in doc["plays"]}
    added = 0
    for r in BATCH_0725:
        k = ("2026-07-25", r[1], r[3], r[4])
        if k not in have:
            doc["plays"].append(rec("2026-07-25", CONFIG, r)); added += 1
    json.dump(doc, open(LOG, "w"), indent=1)
    graded = [x for x in doc["plays"] if x["result"] in ("W", "L")]
    w = sum(1 for x in graded if x["result"] == "W")
    fs = [x for x in graded if x["prop_type"] == "Fantasy Score"]
    fsw = sum(1 for x in fs if x["result"] == "W")
    print("seeded +%d (log now %d plays, %d graded)" % (added, len(doc["plays"]), len(graded)))
    print("running record: %d-%d (%.0f%%)  |  FS: %d-%d" % (
        w, len(graded)-w, 100*w/max(1,len(graded)), fsw, len(fs)-fsw))
    print("log:", LOG)


main()
