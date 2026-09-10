"""Standalone HTML report for a scored league.

Renders the same page for any league the adapters can load -- the bundled
sample, or a real Sleeper league on a machine that can reach the API. There is
no separate "demo" page: the demo is this renderer pointed at the sample data,
so what a real league produces cannot drift from what the demo shows.

Output is a single self-contained file. The only external requests it makes are
for the two Google fonts; everything else, data included, ships inside it.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Sequence

from .matchmaker import Proposal, find_trades, pitch
from .scoring import LeagueScore, TeamScore
from .values import ValueBook

DATA_PLACEHOLDER = "__REPORT_DATA__"
TITLE_PLACEHOLDER = "__REPORT_TITLE__"
DEFAULT_TITLE = "Dynasty Trade Matchmaker"


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def _player_row(vp: Any) -> dict[str, Any]:
    return {
        "name": vp.name,
        "pos": vp.position,
        "age": round(vp.age, 1) if vp.age is not None else None,
        "value": round(vp.value),
        "unmatched": vp.matched_by == "unmatched",
    }


def _team_row(score: TeamScore) -> dict[str, Any]:
    lineup = [
        {
            "slot": slot.code,
            "player": _player_row(player) if player is not None else None,
        }
        for slot, player in score.lineup
    ]
    starting = {id(p) for _, p in score.lineup if p is not None}

    def group(slot_name: str) -> list[dict[str, Any]]:
        return sorted(
            (
                _player_row(p)
                for p in score.roster
                if p.player.slot == slot_name and id(p) not in starting
            ),
            key=lambda r: -r["value"],
        )

    return {
        "id": score.roster_id,
        "name": score.name,
        "manager": score.team.manager,
        "record": score.team.record,
        "starter": round(score.starter_value),
        "player": round(score.player_value),
        "picks": round(score.pick_value),
        "total": round(score.total_value),
        "ovr": score.overall_rank,
        "st": score.starter_rank,
        "gap": score.rank_gap,
        "window": score.window,
        "posture": score.posture,
        "age": round(score.age, 1),
        "cap": round(score.capital_share * 100),
        "surplus": {k: round(v) for k, v in sorted(score.surplus.items(), key=lambda kv: -kv[1])},
        "deficit": {k: round(v) for k, v in sorted(score.deficit.items(), key=lambda kv: -kv[1])},
        "lineup": lineup,
        "bench": group("BENCH"),
        "taxi": group("TAXI"),
        "ir": group("IR"),
        "pickList": sorted(
            (
                {"label": vp.label, "value": round(vp.value), "own": vp.pick.is_own}
                for vp in score.picks
            ),
            key=lambda p: (-p["value"], p["label"]),
        ),
    }


def _proposal_row(proposal: Proposal) -> dict[str, Any]:
    return {
        "score": round(proposal.score),
        "mutuality": round(proposal.mutuality, 2),
        "multi": proposal.is_multi_team,
        "sides": [
            {
                "team": side.team.name,
                "id": side.team.roster_id,
                "window": side.window,
                "sends": [
                    {"label": a.label, "value": round(a.value), "pick": a.is_pick}
                    for a in side.sends
                ],
                "gets": [
                    {"label": a.label, "value": round(a.value), "pick": a.is_pick}
                    for a in side.receives
                ],
                "lineup": round(side.lineup_gain),
                "market": round(side.market_delta),
                "capital": round(side.capital_delta),
                "youth": round(side.youth_delta),
            }
            for side in proposal.sides
        ],
        "pitches": [pitch(proposal, i) for i in range(len(proposal.sides))],
    }


def build_report_data(
    scored: LeagueScore,
    book: ValueBook,
    proposals: Sequence[Proposal] | None = None,
    focus_roster: int | None = None,
    source: str = "live",
    limit: int = 8,
    title: str | None = None,
    exclude_positions: Any = (),
    win_now: bool = False,
    stubborn: Any = (),
) -> dict[str, Any]:
    """Everything the page needs, as plain JSON-able data."""
    settings = scored.settings
    league = scored.league
    if proposals is None:
        proposals = find_trades(
            scored, book, limit=limit, multi_team=True,
            exclude_positions=exclude_positions,
            win_now=win_now, stubborn=stubborn,
        )

    ordered = sorted(scored.teams, key=lambda t: t.overall_rank)

    # A sample pick from each horizon, to show the labelling changing.
    seasons = sorted({p.pick.season for t in scored.teams for p in t.picks})
    sample_picks = []
    for season in seasons[:3]:
        for rnd in (1, 3):
            label = book.pick_label(season, rnd, 3, teams=settings.teams)
            value = book.pick_value(label, settings.superflex, settings.teams)
            if value > 0:
                sample_picks.append({"label": label, "v": round(value)})

    return {
        "meta": {
            "title": title or DEFAULT_TITLE,
            "source": source,
            "generated": datetime.datetime.now().strftime("%d %b %Y"),
            "focus": focus_roster,
            "excluded": sorted({p.upper() for p in exclude_positions}),
            "winNow": bool(win_now),
        },
        "league": {
            "name": league.name,
            "fmt": league.format_label,
            "season": league.season,
            "starters": [s.code for s in settings.starter_slots],
            "bench": settings.bench_slots,
            "taxi": settings.taxi_slots,
            "ir": settings.ir_slots,
            "rounds": settings.draft_rounds,
            "superflex": settings.superflex,
        },
        "demand": {k: round(v, 2) for k, v in scored.demand.items()},
        "replacement": {k: round(v) for k, v in scored.replacement.items()},
        "coverage": round(scored.coverage_skill * 100, 1),
        "unmatched": len(scored.unmatched),
        "teams": [_team_row(t) for t in ordered],
        "proposals": [_proposal_row(p) for p in proposals],
        "picks": sample_picks,
    }


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def render(data: dict[str, Any]) -> str:
    payload = json.dumps(data, separators=(",", ":"))
    if "</script" in payload:
        payload = payload.replace("</script", "<\\/script")
    # The title is written into the tag rather than set from script, because
    # that is where a gallery reads a page's name from.
    title = str(data.get("meta", {}).get("title") or DEFAULT_TITLE)
    title = title.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
    return (
        TEMPLATE.replace(TITLE_PLACEHOLDER, title)
        .replace(DATA_PLACEHOLDER, payload)
    )


def write_report(data: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    return out


__all__ = [
    "build_report_data", "render", "write_report", "TEMPLATE",
    "DEFAULT_TITLE", "DATA_PLACEHOLDER", "TITLE_PLACEHOLDER",
]
TEMPLATE = r"""<title>__REPORT_TITLE__</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,600;0,9..144,700;1,9..144,500&family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap">
<style>
:root{
  --ground:#F3F2F3; --surface:#FCFBFC; --sunken:#F7F6F8; --raised:#FFFFFF;
  --ink:#1A191D; --body:#605D66; --muted:#8B8893; --faint:#B4B1B8;
  --line:#E7E5E9; --hair:#F0EFF2;
  /* --neon is the graphic value; --neon-ink is the one that survives on light. */
  --uv:#5B00FF; --uv-soft:#EFE6FF; --uv-glow:124,0,255;
  --neon:#CCFF00; --neon-ink:#5F7D00; --neon-soft:#F5FFD6; --neon-glow:204,255,0;
  --aqua:#00C2D6;
  --w-contender:#8A6838; --w-contender-bg:#F4F1EB;
  --w-topheavy:#96574B; --w-topheavy-bg:#F5EEEC;
  --w-retooler:#546E82; --w-retooler-bg:#EDF1F4;
  --w-stuck:#75727B;   --w-stuck-bg:#F1F0F2;
  --w-rebuild:#4A7062;  --w-rebuild-bg:#EDF3F0;
  --pos:#4A7062; --neg:#96574B;
  --shadow-s:0 1px 2px rgba(19,17,32,.05),0 6px 18px -10px rgba(19,17,32,.16);
  --shadow-l:0 2px 6px rgba(19,17,32,.06),0 26px 60px -28px rgba(19,17,32,.30);
  --grain:.028;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#060510; --surface:#0E0D16; --sunken:#090810; --raised:#141220;
    --ink:#F1EFF4; --body:#9A97A5; --muted:#6E6B7B; --faint:#4C4858;
    --line:#1E1C28; --hair:#171520;
    --uv:#C77DFF; --uv-soft:#1F0F3A; --uv-glow:199,125,255;
    --neon:#E4FF3D; --neon-ink:#E4FF3D; --neon-soft:#232C08; --neon-glow:228,255,61;
    --aqua:#3BFFEA;
    --w-contender:#BE9468; --w-contender-bg:#201A12;
    --w-topheavy:#C57F73; --w-topheavy-bg:#231615;
    --w-retooler:#7C9EB8; --w-retooler-bg:#121C24;
    --w-stuck:#827E8D;   --w-stuck-bg:#181620;
    --w-rebuild:#63AB8B;  --w-rebuild-bg:#101E17;
    --pos:#63AB8B; --neg:#C57F73;
    --shadow-s:0 1px 2px rgba(0,0,0,.5),0 8px 22px -12px rgba(0,0,0,.8);
    --shadow-l:0 2px 8px rgba(0,0,0,.55),0 30px 70px -30px rgba(0,0,0,.95);
    --grain:.05;
  }
}
:root[data-theme="dark"]{
  --ground:#060510; --surface:#0E0D16; --sunken:#090810; --raised:#141220;
  --ink:#F1EFF4; --body:#9A97A5; --muted:#6E6B7B; --faint:#4C4858;
  --line:#1E1C28; --hair:#171520;
  --uv:#C77DFF; --uv-soft:#1F0F3A; --uv-glow:199,125,255;
  --neon:#E4FF3D; --neon-ink:#E4FF3D; --neon-soft:#232C08; --neon-glow:228,255,61;
  --aqua:#3BFFEA;
  --w-contender:#BE9468; --w-contender-bg:#201A12;
  --w-topheavy:#C57F73; --w-topheavy-bg:#231615;
  --w-retooler:#7C9EB8; --w-retooler-bg:#121C24;
  --w-stuck:#827E8D;   --w-stuck-bg:#181620;
  --w-rebuild:#63AB8B;  --w-rebuild-bg:#101E17;
  --pos:#63AB8B; --neg:#C57F73;
  --shadow-s:0 1px 2px rgba(0,0,0,.5),0 8px 22px -12px rgba(0,0,0,.8);
  --shadow-l:0 2px 8px rgba(0,0,0,.55),0 30px 70px -30px rgba(0,0,0,.95);
  --grain:.05;
}

*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  background:var(--ground); color:var(--body);
  font-family:"Plus Jakarta Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15.5px; line-height:1.6; margin:0;
  padding:0 22px; padding-block:0 0;
  -webkit-font-smoothing:antialiased; overflow-x:hidden;
}
h1,h2,h3,h4{color:var(--ink);text-wrap:balance;margin:0;font-family:"Fraunces",Georgia,serif;
  font-weight:600;font-variation-settings:"SOFT" 60,"WONK" 1;letter-spacing:-.018em;font-optical-sizing:auto;
  font-variation-settings:"SOFT" 30,"WONK" 1}
.mono,.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
.wrap{max-width:1120px;margin:0 auto;position:relative;z-index:2}

/* ---------- ambient background ---------- */
#bg{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.orb{position:absolute;border-radius:50%;filter:blur(76px);opacity:.72;will-change:transform}
.orb.a{width:46vw;height:46vw;left:-12vw;top:-10vw;opacity:.62;
  background:radial-gradient(circle at 40% 40%,rgba(var(--uv-glow),.72),transparent 68%);
  animation:drift1 34s ease-in-out infinite}
.orb.b{width:38vw;height:38vw;right:-10vw;top:22vh;
  background:radial-gradient(circle at 50% 50%,rgba(var(--neon-glow),.42),transparent 66%);
  animation:drift2 42s ease-in-out infinite}
.orb.c{width:34vw;height:34vw;left:28vw;bottom:-14vh;
  background:radial-gradient(circle at 50% 50%,rgba(var(--uv-glow),.46),transparent 70%);
  animation:drift3 50s ease-in-out infinite}
@keyframes drift1{0%,100%{transform:translate3d(0,0,0) scale(1)}50%{transform:translate3d(7vw,5vh,0) scale(1.12)}}
@keyframes drift2{0%,100%{transform:translate3d(0,0,0) scale(1)}50%{transform:translate3d(-6vw,8vh,0) scale(.9)}}
@keyframes drift3{0%,100%{transform:translate3d(0,0,0) scale(1)}50%{transform:translate3d(5vw,-7vh,0) scale(1.15)}}
#spot{position:fixed;inset:0;z-index:1;pointer-events:none;
  background:radial-gradient(520px circle at var(--mx,50%) var(--my,-20%),
    rgba(var(--uv-glow),.17),transparent 62%);
  transition:background .18s ease-out}
#grain{position:fixed;inset:0;z-index:1;pointer-events:none;opacity:var(--grain);
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)'/%3E%3C/svg%3E");
  mix-blend-mode:overlay}

/* ---------- cursor ---------- */
#cdot,#cring{position:fixed;z-index:9999;pointer-events:none;border-radius:50%;
  left:0;top:0;opacity:0;transition:opacity .3s}
#cdot{width:6px;height:6px;background:var(--neon-ink);margin:-3px 0 0 -3px;
  box-shadow:0 0 14px var(--neon)}
#cring{width:30px;height:30px;border:1.5px solid rgba(var(--uv-glow),.72);margin:-15px 0 0 -15px;
  box-shadow:0 0 22px rgba(var(--uv-glow),.28),inset 0 0 12px rgba(var(--uv-glow),.16);
  transition:opacity .3s,width .22s,height .22s,margin .22s,border-color .22s}
body.cursor-on #cdot,body.cursor-on #cring{opacity:1}
body.cursor-on.hot #cring{width:56px;height:56px;margin:-28px 0 0 -28px;
  border-color:rgba(var(--neon-glow),.75)}
@media (pointer:coarse){#cdot,#cring{display:none}}

/* ---------- loader ---------- */
#loader{position:fixed;inset:0;z-index:10000;background:var(--ground);
  display:grid;place-items:center;transition:opacity .55s ease,visibility .55s}
#loader.gone{opacity:0;visibility:hidden}
.load-in{text-align:center;padding:0 20px}
.load-name{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:clamp(22px,4vw,34px);
  color:var(--ink);letter-spacing:-.03em}
.load-sub{font-variant-numeric:tabular-nums;font-size:11px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--muted);margin-top:10px}
.load-bar{width:min(280px,60vw);height:2px;background:var(--line);margin:22px auto 0;overflow:hidden;border-radius:2px}
.load-bar i{display:block;height:100%;width:40%;border-radius:2px;
  background:linear-gradient(90deg,transparent,var(--uv),var(--neon),transparent);
  box-shadow:0 0 16px rgba(var(--uv-glow),.6);
  animation:sweep 1.05s cubic-bezier(.6,0,.35,1) infinite}
@keyframes sweep{0%{transform:translateX(-120%)}100%{transform:translateX(320%)}}

/* ---------- reveals ---------- */
.rv{opacity:1;transform:none}
body.anim .rv{opacity:0;transform:translateY(26px);filter:blur(6px);
  transition:opacity .78s cubic-bezier(.22,.68,.3,1),transform .78s cubic-bezier(.22,.68,.3,1),filter .78s}
body.anim .rv.in{opacity:1;transform:none;filter:none}

/* ---------- hero ---------- */
header{padding-block:clamp(56px,11vh,104px) 40px;position:relative}
.eyebrow{display:inline-flex;align-items:center;gap:10px;
  font-variant-numeric:tabular-nums;font-size:11px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);margin:0 0 22px;
  border:1px solid var(--line);background:color-mix(in srgb,var(--surface) 70%,transparent);
  padding:7px 13px;border-radius:100px;backdrop-filter:blur(8px)}
.eyebrow .pulse{width:7px;height:7px;border-radius:50%;background:rgb(var(--neon-glow));
  box-shadow:0 0 0 0 rgba(var(--neon-glow),.8);animation:pulse 2.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(var(--neon-glow),.7)}
  70%{box-shadow:0 0 0 12px rgba(var(--neon-glow),0)}100%{box-shadow:0 0 0 0 rgba(var(--neon-glow),0)}}
h1{font-size:clamp(42px,8.6vw,96px);line-height:.94;font-weight:700;letter-spacing:-.035em;max-width:15ch}
/* Ends on --neon-ink, not --neon: pure chartreuse is invisible on a light
   ground. In dark the two are the same colour, so nothing is lost there. */
h1 .uv{background:linear-gradient(100deg,var(--uv) 5%,var(--aqua) 48%,var(--neon-ink) 94%);
  -webkit-background-clip:text;background-clip:text;color:transparent}
h1 .word{display:inline-block}
body.anim h1 .word{opacity:0;transform:translateY(.5em) rotate(2deg);
  animation:word .74s cubic-bezier(.2,.7,.25,1) forwards;animation-delay:calc(var(--w)*46ms + .12s)}
@keyframes word{to{opacity:1;transform:none}}
.lede{font-size:clamp(16px,2vw,19.5px);max-width:56ch;margin:26px 0 0;color:var(--body)}
.chips{display:flex;flex-wrap:wrap;gap:9px;margin-top:30px}
.chip{display:inline-flex;align-items:center;gap:9px;font-variant-numeric:tabular-nums;
  font-size:12px;border:1px solid var(--line);background:var(--surface);
  padding:8px 13px;border-radius:100px;color:var(--ink);
  transition:transform .28s cubic-bezier(.2,.8,.3,1),border-color .28s,box-shadow .28s}
.chip:hover{transform:translateY(-3px);border-color:var(--uv);box-shadow:0 8px 20px -10px rgba(var(--uv-glow),.6)}
.chip .k{color:var(--muted)}
.chip b{font-weight:600}
.note{margin-top:26px;padding:14px 16px;border-radius:14px;font-size:13.5px;
  border:1px solid var(--line);background:color-mix(in srgb,var(--surface) 76%,transparent);
  backdrop-filter:blur(10px);position:relative;overflow:hidden;max-width:74ch}
.note::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,rgb(var(--uv-glow)),rgb(var(--neon-glow)))}
.note b{color:var(--ink)}
.scrollcue{display:flex;align-items:center;gap:10px;margin-top:38px;
  font-variant-numeric:tabular-nums;font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--faint)}
.scrollcue i{display:block;width:30px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,rgb(var(--uv-glow)),rgb(var(--neon-glow)));
  animation:cue 2.1s ease-in-out infinite;transform-origin:left}
@keyframes cue{0%,100%{transform:scaleX(.35);opacity:.45}50%{transform:scaleX(1);opacity:1}}

/* ---------- ticker ---------- */
.ticker{position:relative;overflow:hidden;border-block:1px solid var(--line);
  padding-block:11px;margin-top:26px;
  -webkit-mask-image:linear-gradient(90deg,transparent,#000 9%,#000 91%,transparent);
  mask-image:linear-gradient(90deg,transparent,#000 9%,#000 91%,transparent)}
.ticker .run{display:flex;gap:34px;white-space:nowrap;width:max-content;
  animation:run 34s linear infinite;font-variant-numeric:tabular-nums;
  font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.ticker .run span{display:inline-flex;align-items:center;gap:10px}
.ticker .run em{font-style:normal;color:var(--uv);text-shadow:0 0 14px rgba(var(--uv-glow),.45)}
@keyframes run{to{transform:translateX(-50%)}}

/* ---------- sections ---------- */
section{padding-block:clamp(58px,9vh,98px) 0;position:relative}
.shead{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:10px}
.shead h2{font-size:clamp(27px,4.5vw,46px);line-height:1.02;font-weight:600;letter-spacing:-.03em}
.tag{font-variant-numeric:tabular-nums;font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--uv);padding-bottom:8px}
.sdek{max-width:64ch;margin:0 0 30px;color:var(--muted);font-size:15px}
.sdek b{color:var(--ink);font-weight:600}

/* ---------- stat row ---------- */
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.stat{position:relative;padding:24px;border-radius:18px;border:1px solid var(--line);
  background:var(--surface);overflow:hidden;
  transition:transform .4s cubic-bezier(.2,.8,.3,1),box-shadow .4s,border-color .4s}
.stat:hover{transform:translateY(-5px);border-color:var(--uv);box-shadow:var(--shadow-l)}
.stat::after{content:"";position:absolute;inset:auto -30% -60% -30%;height:120px;
  background:radial-gradient(ellipse at 50% 0,rgba(var(--uv-glow),.16),transparent 70%);
  opacity:0;transition:opacity .4s}
.stat:hover::after{opacity:1}
.stat .fig{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:clamp(34px,5.2vw,50px);
  color:var(--ink);letter-spacing:-.04em;line-height:1}
.stat .lbl{font-size:13.5px;color:var(--muted);margin-top:10px;line-height:1.5}
.stat .k{font-variant-numeric:tabular-nums;font-size:10px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--uv);margin-bottom:14px;display:block}

/* ---------- board ---------- */
.boardwrap{border-radius:20px;border:1px solid var(--line);background:var(--surface);
  overflow:hidden;box-shadow:var(--shadow-s)}
.scroller{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:900px;font-size:13.5px}
thead th{font-variant-numeric:tabular-nums;font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted);font-weight:500;text-align:right;
  padding:15px 12px;border-bottom:1px solid var(--line);background:var(--sunken);white-space:nowrap}
thead th.l{text-align:left}
tbody td{padding:13px 12px;border-bottom:1px solid var(--hair);text-align:right;
  white-space:nowrap;position:relative}
tbody td.l{text-align:left}
tbody tr{transition:background .25s}
tbody tr:hover{background:var(--uv-soft)}
tbody tr:last-child td{border-bottom:0}
tbody tr td:first-child::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;
  background:var(--uv);transform:scaleY(0);transition:transform .3s;transform-origin:center}
tbody tr:hover td:first-child::before{transform:scaleY(1)}
.tname{color:var(--ink);font-weight:600;font-size:14px}
.tmgr{color:var(--faint);font-size:11px;font-variant-numeric:tabular-nums;margin-top:1px}
.bar{position:relative;height:5px;border-radius:3px;background:var(--hair);
  margin-top:6px;overflow:hidden;min-width:70px}
.bar i{position:absolute;inset:0 auto 0 0;border-radius:3px;
  background:linear-gradient(90deg,rgb(var(--uv-glow)),rgb(var(--neon-glow)));width:0;
  transition:width 1.1s cubic-bezier(.2,.8,.25,1)}
.rk{color:var(--muted)}
.tot{color:var(--ink);font-weight:700}
.gap{font-weight:700}
.gap.p{color:var(--w-topheavy)} .gap.n{color:var(--w-retooler)} .gap.z{color:var(--faint);font-weight:400}
.win{display:inline-block;font-variant-numeric:tabular-nums;font-size:10px;
  letter-spacing:.1em;padding:5px 10px;border-radius:100px;font-weight:500;white-space:nowrap}
.win[data-w="CONTENDER"]{color:var(--w-contender);background:var(--w-contender-bg)}
.win[data-w="TOP-HEAVY"]{color:var(--w-topheavy);background:var(--w-topheavy-bg)}
.win[data-w="RETOOLER"]{color:var(--w-retooler);background:var(--w-retooler-bg)}
.win[data-w="STUCK"]{color:var(--w-stuck);background:var(--w-stuck-bg)}
.win[data-w="REBUILD"]{color:var(--w-rebuild);background:var(--w-rebuild-bg)}
.shape{font-variant-numeric:tabular-nums;font-size:11px}
.shape .s{color:var(--pos)} .shape .d{color:var(--neg)} .shape .none{color:var(--faint)}

/* ---------- window cards ---------- */
.wins{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-top:26px}
.wc{position:relative;padding:20px 18px;border-radius:16px;border:1px solid var(--line);
  background:var(--surface);transform-style:preserve-3d;
  transition:box-shadow .35s,border-color .35s}
.wc:hover{box-shadow:var(--shadow-l);border-color:color-mix(in srgb,var(--accent-w) 55%,var(--line))}
.wc .dot{width:9px;height:9px;border-radius:50%;background:var(--accent-w);
  box-shadow:0 0 14px var(--accent-w)}
.wc h4{font-variant-numeric:tabular-nums;font-size:11px;letter-spacing:.1em;
  color:var(--accent-w);margin:12px 0 7px;font-weight:600}
.wc p{margin:0;font-size:12.5px;color:var(--muted);line-height:1.45}
.wc .n{position:absolute;top:16px;right:16px;font-variant-numeric:tabular-nums;
  font-size:11px;color:var(--faint)}

/* ---------- trades ---------- */
.props{display:flex;flex-direction:column;gap:22px}
.prop{position:relative;border-radius:22px;border:1px solid var(--line);
  background:var(--surface);overflow:hidden;box-shadow:var(--shadow-s);
  transform-style:preserve-3d;transition:box-shadow .45s,border-color .45s}
.prop:hover{box-shadow:var(--shadow-l);border-color:color-mix(in srgb,var(--uv) 40%,var(--line))}
.prop .glare{position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .4s;
  background:radial-gradient(420px circle at var(--gx,50%) var(--gy,50%),
    rgba(var(--uv-glow),.13),transparent 60%)}
.prop:hover .glare{opacity:1}
.prop.top{border-color:color-mix(in srgb,var(--uv) 55%,var(--line))}
.prop.top::before{content:"";position:absolute;inset:0;border-radius:22px;padding:1px;
  background:linear-gradient(120deg,rgb(var(--uv-glow)),var(--aqua),rgb(var(--neon-glow)));
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none;opacity:.85}
.phead{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:18px 22px;
  border-bottom:1px solid var(--hair);position:relative}
.rank{font-family:"Plus Jakarta Sans",sans-serif;font-size:12.5px;font-weight:700;color:var(--surface);
  background:var(--ink);width:30px;height:30px;border-radius:10px;
  display:grid;place-items:center;flex:none}
.prop.top .rank{background:linear-gradient(135deg,rgb(var(--uv-glow)),rgb(var(--neon-glow)));
  color:#0B0912;box-shadow:0 0 24px -4px rgba(var(--neon-glow),.7)}
.ptitle{font-weight:600;color:var(--ink);font-size:17px;font-family:"Fraunces",Georgia,serif;letter-spacing:-.02em}
.pmeta{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.mchip{font-variant-numeric:tabular-nums;font-size:10.5px;color:var(--muted);
  border:1px solid var(--line);padding:5px 9px;border-radius:100px}
.mchip b{color:var(--ink)}
.sides{display:grid;grid-template-columns:1fr auto 1fr;align-items:stretch}
.side{padding:22px}
.swap{display:grid;place-items:center;padding:0 6px;position:relative}
.swap i{display:grid;place-items:center;width:38px;height:38px;border-radius:50%;
  border:1px solid var(--line);background:var(--sunken);color:var(--uv);
  font-variant-numeric:tabular-nums;font-size:15px;font-style:normal;
  transition:transform .5s cubic-bezier(.2,.8,.3,1),border-color .4s}
.prop:hover .swap i{transform:rotate(180deg);border-color:var(--uv)}
.sname{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.sname b{color:var(--ink);font-size:15px;font-weight:600}
.flow .hdr{font-variant-numeric:tabular-nums;font-size:9.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--faint);margin-bottom:7px}
.gets{margin-top:16px}
.arow{display:flex;justify-content:space-between;gap:12px;align-items:baseline;
  font-size:14px;padding:5px 0;border-bottom:1px dashed transparent;
  transition:border-color .25s,padding-left .25s}
.arow:hover{border-bottom-color:var(--hair);padding-left:5px}
.arow .a{color:var(--ink)}
.arow .a.pick{color:var(--uv);font-variant-numeric:tabular-nums;font-size:13px}
.arow .v{font-variant-numeric:tabular-nums;font-size:12.5px;color:var(--muted);flex:none}
.deltas{display:flex;flex-wrap:wrap;gap:7px;margin-top:18px;padding-top:14px;border-top:1px solid var(--hair)}
.d-{font-variant-numeric:tabular-nums;font-size:11px;padding:4px 9px;border-radius:100px;
  background:var(--sunken);color:var(--muted);border:1px solid transparent;transition:border-color .3s}
.d-:hover{border-color:var(--line)}
.d- b{font-weight:600}
.d-.up b{color:var(--pos)} .d-.down b{color:var(--neg)}
.pitch{padding:20px 22px;background:var(--sunken);border-top:1px solid var(--hair)}
.pitchhead{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.pitchhead .lb{font-variant-numeric:tabular-nums;font-size:9.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--faint)}
.who{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
button{font-variant-numeric:tabular-nums;font-size:11px;border:1px solid var(--line);
  background:var(--surface);color:var(--body);padding:7px 13px;border-radius:100px;
  cursor:pointer;transition:transform .22s cubic-bezier(.2,.8,.3,1),border-color .25s,
    color .25s,background .25s,box-shadow .25s}
button:hover{border-color:var(--uv);color:var(--uv);transform:translateY(-2px);
  box-shadow:0 8px 18px -10px rgba(var(--uv-glow),.7)}
button:active{transform:translateY(0) scale(.97)}
button:focus-visible{outline:2px solid var(--uv);outline-offset:3px}
button[aria-pressed="true"]{background:var(--uv);color:#fff;border-color:var(--uv);
  box-shadow:0 0 20px rgba(var(--uv-glow),.5)}
blockquote{margin:0;font-size:14px;color:var(--body);white-space:pre-wrap;line-height:1.66;
  border-left:2px solid var(--uv);padding-left:16px}

/* ---------- method ---------- */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:24px;
  transition:border-color .35s,box-shadow .35s}
.panel:hover{border-color:var(--uv);box-shadow:var(--shadow-l)}
.panel h3{font-variant-numeric:tabular-nums;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--uv);font-weight:600;margin-bottom:18px}
.kv{display:flex;justify-content:space-between;gap:14px;padding:9px 0;
  border-bottom:1px solid var(--hair);font-size:14px}
.kv:last-of-type{border-bottom:0}
.kv .k{color:var(--body)}
.kv .v{font-variant-numeric:tabular-nums;color:var(--ink);
  font-variant-numeric:tabular-nums;flex:none}
.panel p{font-size:13px;color:var(--muted);margin:16px 0 0;line-height:1.55}

footer{margin-top:clamp(60px,9vh,100px);padding-block:34px 60px;border-top:1px solid var(--line);
  font-size:13.5px;color:var(--muted)}
footer b{color:var(--ink)}
.cmd{font-variant-numeric:tabular-nums;font-size:12.5px;background:var(--sunken);
  border:1px solid var(--hair);padding:14px 16px;border-radius:14px;color:var(--ink);
  overflow-x:auto;white-space:pre;margin-top:16px}

@media (max-width:900px){
  .stats{grid-template-columns:1fr}
}
@media (max-width:760px){
  .sides{grid-template-columns:1fr}
  .swap{padding:2px 0}
  .prop:hover .swap i{transform:rotate(90deg)}
  .grid2{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{animation:none!important;transition:none!important}
  body.anim .rv{opacity:1!important;transform:none!important;filter:none!important}
  #cdot,#cring{display:none}
}
</style>

<div id="bg" aria-hidden="true">
  <div class="orb a"></div><div class="orb b"></div><div class="orb c"></div>
</div>
<div id="spot" aria-hidden="true"></div>
<div id="grain" aria-hidden="true"></div>
<div id="cring" aria-hidden="true"></div><div id="cdot" aria-hidden="true"></div>

<div id="loader" aria-hidden="true">
  <div class="load-in">
    <div class="load-name" id="loadName">Dynasty Trade Matchmaker</div>
    <div class="load-sub">scanning every roster</div>
    <div class="load-bar"><i></i></div>
  </div>
</div>

<div class="wrap">
<header>
  <p class="eyebrow"><span class="pulse"></span><span id="eyebrow">Live league report</span></p>
  <h1 id="headline"></h1>
  <p class="lede rv">Every other tool answers <em>is this fair?</em> — a question about a trade you already imagined. This one reads every roster in the league, finds the value that is sitting idle, and hands you the deals that should happen, with the message to send.</p>
  <div class="chips rv" id="detected"></div>
  <div class="note rv" id="notice"></div>
  <div class="scrollcue rv"><i></i> scroll</div>
</header>

<div class="ticker rv"><div class="run" id="ticker"></div></div>

<section>
  <div class="shead rv"><h2>Nothing here is configured</h2><span class="tag">measured, not assumed</span></div>
  <p class="sdek rv">Team count, scoring, starting slots and flex eligibility are read off the league itself. So are the two numbers every decision below rests on.</p>
  <div class="stats" id="stats"></div>
</section>

<section>
  <div class="shead rv"><h2>The board</h2><span class="tag">deployed vs held</span></div>
  <p class="sdek rv">The diagnostic is the <b>gap</b> between where a roster's starting lineup ranks and where its total value ranks — never the raw number. Two teams can hold near-identical value and be in opposite situations.</p>
  <div class="boardwrap rv">
    <div class="scroller">
      <table>
        <thead><tr>
          <th class="l">Team</th><th>Starters</th><th>Players</th><th>Picks</th>
          <th>Total</th><th>Ovr</th><th>St</th><th>Gap</th>
          <th class="l">Window</th><th class="l">Shape</th>
        </tr></thead>
        <tbody id="board"></tbody>
      </table>
    </div>
  </div>
  <div class="wins" id="wins"></div>
</section>

<section>
  <div class="shead rv"><h2>Trades that should happen</h2><span class="tag">both sides willing</span></div>
  <p class="sdek rv" id="tradedek">A proposal only exists if <b>each side improves relative to its own window</b>. A contender will overpay to raise its lineup; a rebuilder will not be fleeced. That asymmetry is why these clear at all — a fairness grader would call several of them lopsided.</p>
  <div class="props" id="props"></div>
</section>

<section>
  <div class="shead rv"><h2>How it decides</h2><span class="tag">no model in the matching path</span></div>
  <div class="grid2">
    <div class="panel rv">
      <h3>Positional demand, measured</h3>
      <div id="demand"></div>
      <p>Dedicated slots are counted; flex demand is measured from which positions actually win flex across every optimal lineup. Under full PPR that lands on receivers. Change the scoring and it moves on its own — there is no table of positional multipliers anywhere in the system.</p>
    </div>
    <div class="panel rv">
      <h3>Picks on the player scale</h3>
      <div id="picks"></div>
      <p>The source file ships pick <span class="mono">ECR</span> — a rank, not a value — so picks and players arrive incomparable. A monotone ECR&rarr;value curve fitted from the player board converts them. Labels change by horizon too: exact for the next draft, tier after that, round only beyond.</p>
    </div>
  </div>
</section>

<footer class="rv">
  <p><b>Free, non-commercial use.</b> Sleeper's public API is licensed for non-commercial use only, so this ships with every feature enabled and no paid tier until they say otherwise in writing. It reads only — no password, no OAuth, no writes to any league.</p>
  <p style="margin-top:14px">Values from <b>DynastyProcess</b> (Tan Ho, Joe Sydlowski). League data from the <b>Sleeper API</b>.</p>
  <div class="cmd">python3 -m src.cli report &lt;your-sleeper-username&gt;
python3 -m src.cli report &lt;league_id&gt; --exclude-position QB</div>
</footer>
</div>

<script id="data" type="application/json">__REPORT_DATA__</script>
<script>
(function(){
  var D = JSON.parse(document.getElementById('data').textContent);
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduce) document.body.classList.add('anim');

  var esc = function(s){ return String(s).replace(/[&<>"]/g, function(c){
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]; }); };
  var n = function(v){ return Number(v).toLocaleString('en-US'); };
  var sign = function(v){ return (v>0?'+':'') + n(v); };
  var ME = D.meta.focus, L = D.league;
  var byId = {}; D.teams.forEach(function(t){ byId[t.id] = t; });
  var mine = ME != null ? byId[ME] : null;

  /* ---- loader ---- */
  document.getElementById('loadName').textContent = L.name;
  var hideLoader = function(){ document.getElementById('loader').classList.add('gone'); };
  if (reduce) hideLoader(); else setTimeout(hideLoader, 900);

  /* ---- hero ---- */
  document.getElementById('eyebrow').textContent =
    L.name + ' · ' + L.season + ' · ' + L.fmt;
  var words = ['It', 'finds', 'the', '<span class="uv">trade</span>.'];
  document.getElementById('headline').innerHTML = words.map(function(w, i){
    return '<span class="word" style="--w:' + i + '">' + w + '</span>';
  }).join(' ');

  document.getElementById('detected').innerHTML = [
    ['Format', L.fmt],
    ['Starters', L.starters.join(' ')],
    ['Bench / taxi', L.bench + ' / ' + L.taxi],
    ['Coverage', D.coverage.toFixed(1) + '%']
  ].map(function(c){
    return '<span class="chip"><span class="k">' + esc(c[0]) + '</span><b>' + esc(c[1]) + '</b></span>';
  }).join('');

  var ex = (D.meta.excluded || []);
  document.getElementById('notice').innerHTML = (D.meta.source === 'sample'
    ? '<b>Sample league.</b> Real players and real DynastyProcess values; the managers are invented. Point the same command at a real league and this page regenerates from it.'
    : '<b>' + esc(L.name) + '</b> · generated ' + esc(D.meta.generated) + ' from the roster sheet and live DynastyProcess values.')
    + (mine ? ' Your team is <b>' + esc(mine.name) + '</b>.' : '')
    + (ex.length ? ' <b>' + esc(ex.join(', ')) + '</b> is excluded from every trade at the commissioner\'s direction — this league does not pay for it.' : '')
    + (D.meta.winNow ? ' Every manager here believes they can win, so no proposal asks anyone to accept a worse starting lineup.' : '');

  var tick = [
    ['teams', L.starters ? D.teams.length : 0],
    ['format', L.fmt],
    ['coverage', D.coverage.toFixed(1) + '%'],
    ['proposals', D.proposals.length],
    ['no model in the matching path', ''],
    ['deterministic output', '']
  ].map(function(t){
    return '<span>' + esc(t[0]) + (t[1] !== '' ? ' <em>' + esc(t[1]) + '</em>' : '') + '</span>';
  }).join('');
  document.getElementById('ticker').innerHTML = tick + tick;

  /* ---- stats ---- */
  var dm = D.demand, rp = D.replacement;
  var deep = Object.keys(dm).sort(function(a,b){ return dm[b]-dm[a]; })[0];
  var statData = [
    ['flex demand', dm[deep].toFixed(2), deep + ' starters each team must field, counting the share of flex slots ' + deep + ' actually wins under this scoring.'],
    ['replacement', n(rp.RB), 'The first running back this league cannot start. It is the line between a tradeable asset and a body.'],
    ['found', String(D.proposals.length), 'Deals where both managers come out ahead in their own terms. Generated in well under a second.']
  ];
  document.getElementById('stats').innerHTML = statData.map(function(s, i){
    return '<div class="stat rv" style="transition-delay:' + (i*70) + 'ms">'
      + '<span class="k">' + esc(s[0]) + '</span>'
      + '<div class="fig" data-count="' + esc(s[1]) + '">' + esc(s[1]) + '</div>'
      + '<div class="lbl">' + esc(s[2]) + '</div></div>';
  }).join('');

  /* ---- board ---- */
  var maxTotal = Math.max.apply(null, D.teams.map(function(t){ return t.total; }));
  var shape = function(t){
    var out = [];
    Object.keys(t.surplus).forEach(function(k){ out.push('<span class="s">'+k+' +'+n(t.surplus[k])+'</span>'); });
    Object.keys(t.deficit).forEach(function(k){ out.push('<span class="d">'+k+' −'+n(t.deficit[k])+'</span>'); });
    return out.length ? out.join('  ') : '<span class="none">balanced</span>';
  };
  document.getElementById('board').innerHTML = D.teams.map(function(t){
    var g = t.gap>0?'p':(t.gap<0?'n':'z');
    return '<tr>'
      + '<td class="l"><div class="tname">' + esc(t.name) + '</div>'
      + '<div class="tmgr">' + esc(t.manager) + '</div>'
      + '<div class="bar" data-w="' + Math.round(t.total/maxTotal*100) + '"><i></i></div></td>'
      + '<td class="num">' + n(t.starter) + '</td><td class="num">' + n(t.player) + '</td>'
      + '<td class="num">' + n(t.picks) + '</td><td class="num tot">' + n(t.total) + '</td>'
      + '<td class="num rk">' + t.ovr + '</td><td class="num rk">' + t.st + '</td>'
      + '<td class="num gap ' + g + '">' + (t.gap>0?'+':'') + t.gap + '</td>'
      + '<td class="l"><span class="win" data-w="' + esc(t.window) + '">' + esc(t.window) + '</span></td>'
      + '<td class="l shape">' + shape(t) + '</td></tr>';
  }).join('');

  var winMeta = {
    CONTENDER:['push','both ranks strong','--w-contender'],
    'TOP-HEAVY':['mortgaged','lineup outruns the assets','--w-topheavy'],
    RETOOLER:['consolidate','assets outrun the lineup','--w-retooler'],
    STUCK:['pick a direction','no gap either way','--w-stuck'],
    REBUILD:['hold','both weak, capital rich','--w-rebuild']
  };
  var counts = {}; D.teams.forEach(function(t){ counts[t.window] = (counts[t.window]||0)+1; });
  document.getElementById('wins').innerHTML = Object.keys(winMeta).map(function(k, i){
    var m = winMeta[k];
    return '<div class="wc rv tilt" style="--accent-w:var(' + m[2] + ');transition-delay:' + (i*60) + 'ms">'
      + '<span class="n">' + (counts[k]||0) + '</span><span class="dot"></span>'
      + '<h4>' + esc(k) + '</h4><p><b>' + esc(m[0]) + '</b> — ' + esc(m[1]) + '</p></div>';
  }).join('');

  /* ---- proposals ---- */
  var rows = function(list){
    return list.map(function(a){
      return '<div class="arow"><span class="a' + (a.pick?' pick':'') + '">' + esc(a.label) + '</span>'
        + '<span class="v">' + n(a.value) + '</span></div>';
    }).join('');
  };
  var delta = function(label, v){
    if (!v) return '';
    return '<span class="d- ' + (v>0?'up':'down') + '">' + label + ' <b>' + sign(v) + '</b></span>';
  };
  document.getElementById('props').innerHTML = D.proposals.map(function(p, i){
    var start = 0;
    p.sides.forEach(function(s, k){ if (s.id===ME) start = k; });
    var sides = p.sides.map(function(s, k){
      return (k ? '<div class="swap"><i>⇄</i></div>' : '')
        + '<div class="side"><div class="sname"><b>' + esc(s.team) + '</b>'
        + '<span class="win" data-w="' + esc(s.window) + '">' + esc(s.window) + '</span></div>'
        + '<div class="flow"><div class="hdr">Sends</div>' + rows(s.sends) + '</div>'
        + '<div class="flow gets"><div class="hdr">Gets</div>' + rows(s.gets) + '</div>'
        + '<div class="deltas">' + delta('lineup', s.lineup) + delta('market', s.market)
        + delta('capital', s.capital) + '</div></div>';
    }).join('');
    var who = p.sides.map(function(s, k){
      return '<button type="button" class="whoBtn" data-p="' + i + '" data-s="' + k + '" '
        + 'aria-pressed="' + (k===start) + '">' + esc(s.team) + '</button>';
    }).join('');
    return '<article class="prop rv tilt' + (i===0?' top':'') + '">'
      + '<div class="glare"></div>'
      + '<div class="phead"><span class="rank">' + (i+1) + '</span>'
      + '<span class="ptitle">' + p.sides.map(function(s){return esc(s.team);}).join(' × ') + '</span>'
      + '<span class="pmeta"><span class="mchip">score <b>' + n(p.score) + '</b></span>'
      + '<span class="mchip">mutuality <b>' + p.mutuality.toFixed(2) + '</b></span></span></div>'
      + '<div class="sides">' + sides + '</div>'
      + '<div class="pitch"><div class="pitchhead"><span class="lb">Message to send, from</span>'
      + '<span class="who">' + who + '</span></div>'
      + '<blockquote id="pitch-' + i + '">' + esc(p.pitches[start]) + '</blockquote>'
      + '<div style="margin-top:14px"><button type="button" class="copyBtn" data-p="' + i + '">Copy message</button></div>'
      + '</div></article>';
  }).join('');

  var shown = {};
  D.proposals.forEach(function(p, i){
    var s = 0; p.sides.forEach(function(x, k){ if (x.id===ME) s = k; }); shown[i] = s;
  });
  document.querySelectorAll('.whoBtn').forEach(function(b){
    b.addEventListener('click', function(){
      var i = +b.dataset.p, k = +b.dataset.s;
      shown[i] = k;
      document.getElementById('pitch-'+i).textContent = D.proposals[i].pitches[k];
      document.querySelectorAll('.whoBtn[data-p="'+i+'"]').forEach(function(o){
        o.setAttribute('aria-pressed', String(+o.dataset.s === k));
      });
    });
  });
  document.querySelectorAll('.copyBtn').forEach(function(b){
    b.addEventListener('click', function(){
      var i = +b.dataset.p, text = D.proposals[i].pitches[shown[i]];
      var done = function(ok){ b.textContent = ok ? 'Copied ✓' : 'Select & copy';
        setTimeout(function(){ b.textContent = 'Copy message'; }, 1700); };
      try { navigator.clipboard.writeText(text).then(function(){done(true);},function(){done(false);}); }
      catch(e){ done(false); }
    });
  });

  /* ---- method ---- */
  document.getElementById('demand').innerHTML =
    Object.keys(D.demand).map(function(k){
      return '<div class="kv"><span class="k">' + esc(k) + ' starters per team</span>'
        + '<span class="v">' + D.demand[k].toFixed(2) + '</span></div>';
    }).join('')
    + Object.keys(D.replacement).map(function(k){
      return '<div class="kv"><span class="k">' + esc(k) + ' replacement level</span>'
        + '<span class="v">' + n(D.replacement[k]) + '</span></div>';
    }).join('');
  document.getElementById('picks').innerHTML = D.picks.map(function(p){
    return '<div class="kv"><span class="k mono" style="font-size:13px">' + esc(p.label) + '</span>'
      + '<span class="v">' + n(p.v) + '</span></div>';
  }).join('');

  if (reduce) return;

  /* ---- reveal on scroll ---- */
  var io = window.IntersectionObserver ? new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if (!e.isIntersecting) return;
      e.target.classList.add('in');
      e.target.querySelectorAll && e.target.querySelectorAll('.bar').forEach(function(bar){
        bar.querySelector('i').style.width = bar.dataset.w + '%';
      });
      io.unobserve(e.target);
    });
  }, {rootMargin:'0px 0px -8% 0px', threshold:.12}) : null;

  if (io) {
    document.querySelectorAll('.rv').forEach(function(el){ io.observe(el); });
  } else {
    document.querySelectorAll('.rv').forEach(function(el){ el.classList.add('in'); });
    document.querySelectorAll('.bar').forEach(function(b){
      b.querySelector('i').style.width = b.dataset.w + '%'; });
  }
  // Safety net: nothing stays hidden if the observer never fires.
  setTimeout(function(){
    document.querySelectorAll('.rv:not(.in)').forEach(function(el){ el.classList.add('in'); });
    document.querySelectorAll('.bar').forEach(function(b){
      var i = b.querySelector('i'); if (!i.style.width) i.style.width = b.dataset.w + '%'; });
  }, 2600);

  /* ---- cursor + spotlight ---- */
  var dot = document.getElementById('cdot'), ring = document.getElementById('cring');
  var mx = innerWidth/2, my = innerHeight/2, rx = mx, ry = my;
  if (!window.matchMedia('(pointer: coarse)').matches) {
    document.body.classList.add('cursor-on');
    addEventListener('mousemove', function(e){
      mx = e.clientX; my = e.clientY;
      dot.style.transform = 'translate3d(' + mx + 'px,' + my + 'px,0)';
      document.documentElement.style.setProperty('--mx', mx + 'px');
      document.documentElement.style.setProperty('--my', my + 'px');
    }, {passive:true});
    (function loop(){
      rx += (mx - rx) * .16; ry += (my - ry) * .16;
      ring.style.transform = 'translate3d(' + rx + 'px,' + ry + 'px,0)';
      requestAnimationFrame(loop);
    })();
    document.querySelectorAll('button,a,.chip,.prop,.stat,.wc').forEach(function(el){
      el.addEventListener('mouseenter', function(){ document.body.classList.add('hot'); });
      el.addEventListener('mouseleave', function(){ document.body.classList.remove('hot'); });
    });
  }

  /* ---- 3D tilt + glare ---- */
  document.querySelectorAll('.tilt').forEach(function(card){
    var max = card.classList.contains('prop') ? 3.2 : 7;
    card.addEventListener('mousemove', function(e){
      var r = card.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      card.style.transform = 'perspective(1000px) rotateY(' + ((px-.5)*max*2).toFixed(2)
        + 'deg) rotateX(' + ((.5-py)*max*2).toFixed(2) + 'deg) translateZ(0)';
      card.style.setProperty('--gx', (px*100) + '%');
      card.style.setProperty('--gy', (py*100) + '%');
    });
    card.addEventListener('mouseleave', function(){ card.style.transform = ''; });
  });

  /* ---- count up ---- */
  var counted = new WeakSet();
  var cio = window.IntersectionObserver ? new IntersectionObserver(function(es){
    es.forEach(function(e){
      if (!e.isIntersecting || counted.has(e.target)) return;
      counted.add(e.target);
      var raw = e.target.dataset.count, num = parseFloat(raw.replace(/,/g,''));
      if (isNaN(num)) return;
      var dec = (raw.indexOf('.') > -1) ? (raw.length - raw.indexOf('.') - 1) : 0;
      var t0 = performance.now(), dur = 900;
      (function step(t){
        var k = Math.min(1, (t - t0)/dur), eased = 1 - Math.pow(1-k, 3);
        e.target.textContent = (num*eased).toFixed(dec).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        if (k < 1) requestAnimationFrame(step); else e.target.textContent = raw;
      })(t0);
    });
  }, {threshold:.5}) : null;
  if (cio) document.querySelectorAll('[data-count]').forEach(function(el){ cio.observe(el); });
})();
</script>
"""
