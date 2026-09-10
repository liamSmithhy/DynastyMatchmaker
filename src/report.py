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
) -> dict[str, Any]:
    """Everything the page needs, as plain JSON-able data."""
    settings = scored.settings
    league = scored.league
    if proposals is None:
        proposals = find_trades(scored, book, limit=limit, multi_team=True)

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
            "source": source,
            "generated": datetime.datetime.now().strftime("%d %b %Y"),
            "focus": focus_roster,
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
    return TEMPLATE.replace(DATA_PLACEHOLDER, payload)


def write_report(data: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    return out


__all__ = ["build_report_data", "render", "write_report", "TEMPLATE"]
TEMPLATE = r"""<title>Dynasty Trade Matchmaker</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,500;0,600;1,400&display=swap">
<style>
:root{
  --ground:#EEF1F5; --surface:#FFFFFF; --sunken:#F5F7FA;
  --ink:#151A23; --body:#39414F; --muted:#68717F; --faint:#98A1B0;
  --line:#D9DEE6; --hair:#E7EBF1;
  --accent:#1F4E79; --accent-soft:#E4EDF5;
  --w-contender:#9A5410; --w-contender-bg:#F7EDE1;
  --w-topheavy:#A93221; --w-topheavy-bg:#F8E8E5;
  --w-retooler:#2A6285; --w-retooler-bg:#E3EEF5;
  --w-stuck:#5D6675;   --w-stuck-bg:#ECEEF2;
  --w-rebuild:#256A4E;  --w-rebuild-bg:#E1EFE8;
  --pos:#256A4E; --neg:#A93221;
  --shadow:0 1px 2px rgba(21,26,35,.06),0 8px 24px -12px rgba(21,26,35,.18);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#10141B; --surface:#181D26; --sunken:#141922;
    --ink:#E9ECF3; --body:#BEC6D4; --muted:#8B95A6; --faint:#6B7484;
    --line:#2A313D; --hair:#232935;
    --accent:#7EB4DE; --accent-soft:#1B2C3B;
    --w-contender:#E0A05C; --w-contender-bg:#2E2418;
    --w-topheavy:#E58273; --w-topheavy-bg:#31201D;
    --w-retooler:#7FB6D8; --w-retooler-bg:#1A2833;
    --w-stuck:#9AA3B2;   --w-stuck-bg:#22262E;
    --w-rebuild:#6BBE97;  --w-rebuild-bg:#17291F;
    --pos:#6BBE97; --neg:#E58273;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px -14px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --ground:#10141B; --surface:#181D26; --sunken:#141922;
  --ink:#E9ECF3; --body:#BEC6D4; --muted:#8B95A6; --faint:#6B7484;
  --line:#2A313D; --hair:#232935;
  --accent:#7EB4DE; --accent-soft:#1B2C3B;
  --w-contender:#E0A05C; --w-contender-bg:#2E2418;
  --w-topheavy:#E58273; --w-topheavy-bg:#31201D;
  --w-retooler:#7FB6D8; --w-retooler-bg:#1A2833;
  --w-stuck:#9AA3B2;   --w-stuck-bg:#22262E;
  --w-rebuild:#6BBE97;  --w-rebuild-bg:#17291F;
  --pos:#6BBE97; --neg:#E58273;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px -14px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{
  background:var(--ground); color:var(--body);
  font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.6; padding:0 20px; padding-block:0 64px;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1080px;margin:0 auto}
h1,h2,h3,h4{color:var(--ink);text-wrap:balance;margin:0}
.mono,.num{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}

header{padding-block:56px 34px;border-bottom:2px solid var(--ink)}
.eyebrow{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted);margin:0 0 18px}
h1{font-family:"IBM Plex Serif",Georgia,serif;font-weight:600;
  font-size:clamp(34px,6.2vw,56px);line-height:1.04;letter-spacing:-.015em}
.lede{font-size:clamp(16px,2.2vw,19px);max-width:60ch;margin:18px 0 0;color:var(--body)}
.lede em{font-family:"IBM Plex Serif",Georgia,serif;font-style:italic;color:var(--ink)}
.detected{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:26px}
.chip{display:inline-flex;align-items:center;gap:8px;font-family:"IBM Plex Mono",monospace;
  font-size:12px;border:1px solid var(--line);background:var(--surface);
  padding:6px 11px;border-radius:2px;color:var(--ink)}
.chip b{font-weight:600}
.chip .k{color:var(--muted);font-weight:400}
.notice{margin-top:22px;padding:12px 14px;border-left:3px solid var(--accent);
  background:var(--accent-soft);font-size:13.5px;color:var(--body);border-radius:0 2px 2px 0}
.notice b{color:var(--ink)}

section{padding-block:44px 0}
.shead{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:6px}
.shead h2{font-family:"IBM Plex Serif",Georgia,serif;font-weight:600;
  font-size:clamp(21px,3vw,27px);letter-spacing:-.01em}
.shead .tag{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint)}
.sdek{max-width:66ch;margin:0 0 22px;color:var(--muted);font-size:14.5px}

.claims{display:grid;grid-template-columns:repeat(3,1fr);
  border:1px solid var(--line);background:var(--surface);border-radius:3px;overflow:hidden}
.claim{padding:20px;border-right:1px solid var(--hair)}
.claim:last-child{border-right:0}
.claim .fig{font-family:"IBM Plex Mono",monospace;font-size:26px;font-weight:500;
  color:var(--ink);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.claim .lbl{font-size:13px;color:var(--muted);margin-top:5px;line-height:1.45}

.scroller{overflow-x:auto;border:1px solid var(--line);border-radius:3px;background:var(--surface)}
table{border-collapse:collapse;width:100%;min-width:840px;font-size:13.5px}
thead th{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);font-weight:500;text-align:right;
  padding:11px 10px;border-bottom:1px solid var(--line);background:var(--sunken);white-space:nowrap}
thead th.l{text-align:left}
tbody td{padding:9px 10px;border-bottom:1px solid var(--hair);text-align:right;white-space:nowrap}
tbody td.l{text-align:left}
tbody tr:last-child td{border-bottom:0}
tbody tr.me{background:var(--accent-soft)}
.tname{color:var(--ink);font-weight:600}
.tmgr{color:var(--faint);font-size:11.5px;font-family:"IBM Plex Mono",monospace}
.you{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:9.5px;
  letter-spacing:.1em;background:var(--accent);color:var(--surface);
  padding:2px 5px;border-radius:2px;margin-left:6px;vertical-align:1px}
.rk{color:var(--muted)}
.tot{color:var(--ink);font-weight:600}
.gap{font-weight:600}
.gap.p{color:var(--w-topheavy)} .gap.n{color:var(--w-retooler)} .gap.z{color:var(--faint);font-weight:400}
.win{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  letter-spacing:.06em;padding:3px 8px;border-radius:2px;font-weight:500;white-space:nowrap}
.win[data-w="CONTENDER"]{color:var(--w-contender);background:var(--w-contender-bg)}
.win[data-w="TOP-HEAVY"]{color:var(--w-topheavy);background:var(--w-topheavy-bg)}
.win[data-w="RETOOLER"]{color:var(--w-retooler);background:var(--w-retooler-bg)}
.win[data-w="STUCK"]{color:var(--w-stuck);background:var(--w-stuck-bg)}
.win[data-w="REBUILD"]{color:var(--w-rebuild);background:var(--w-rebuild-bg)}
.shape{font-family:"IBM Plex Mono",monospace;font-size:11.5px}
.shape .s{color:var(--pos)} .shape .d{color:var(--neg)} .shape .none{color:var(--faint)}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:14px;font-size:12.5px;color:var(--muted)}
.legend div{display:flex;align-items:center;gap:7px}
.dot{width:9px;height:9px;border-radius:50%;flex:none}

/* rosters */
.rosters{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}
.rcard{background:var(--surface);border:1px solid var(--line);border-radius:3px;overflow:hidden}
.rcard.me{grid-column:1/-1;border-color:var(--accent);box-shadow:var(--shadow)}
.rhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 16px;
  background:var(--sunken);border-bottom:1px solid var(--line)}
.rhead b{color:var(--ink);font-size:14.5px}
.rhead .rmeta{margin-left:auto;font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted)}
.rhead .rmeta b{font-size:11.5px}
.rbody{padding:14px 16px}
.rgroup{margin-bottom:14px}
.rgroup:last-child{margin-bottom:0}
.rglabel{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint);margin-bottom:6px;
  padding-bottom:4px;border-bottom:1px solid var(--hair)}
.prow{display:grid;grid-template-columns:44px 1fr auto auto;gap:10px;align-items:baseline;
  font-size:13px;padding:2.5px 0}
.prow .slot{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--faint)}
.prow .pn{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prow .pp{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--muted)}
.prow .pv{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted);
  font-variant-numeric:tabular-nums;text-align:right}
.prow.empty .pn{color:var(--faint);font-style:italic}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.pk{font-family:"IBM Plex Mono",monospace;font-size:11px;padding:3px 7px;border-radius:2px;
  background:var(--sunken);border:1px solid var(--hair);color:var(--body);white-space:nowrap}
.pk.traded{border-color:var(--accent);color:var(--accent)}
.pk i{font-style:normal;color:var(--faint)}

/* proposals */
.props{display:flex;flex-direction:column;gap:20px}
.prop{background:var(--surface);border:1px solid var(--line);border-radius:3px;
  box-shadow:var(--shadow);overflow:hidden}
.prop.top{border-color:var(--accent)}
.phead{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 18px;
  background:var(--sunken);border-bottom:1px solid var(--line)}
.rank{font-family:"IBM Plex Mono",monospace;font-size:12px;font-weight:600;color:var(--surface);
  background:var(--ink);width:24px;height:24px;border-radius:2px;display:grid;place-items:center;flex:none}
.prop.top .rank{background:var(--accent)}
.pmeta{margin-left:auto;display:flex;gap:16px;font-family:"IBM Plex Mono",monospace;
  font-size:11.5px;color:var(--muted)}
.pmeta b{color:var(--ink);font-weight:600}
.ptitle{font-weight:600;color:var(--ink);font-size:14.5px}
.sides{display:grid;grid-template-columns:1fr 1fr}
.side{padding:18px;border-right:1px solid var(--hair)}
.side:last-child{border-right:0}
.sname{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:14px}
.sname b{color:var(--ink);font-size:14px}
.flow{display:flex;flex-direction:column;gap:5px;margin-bottom:6px}
.flow .hdr{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint);margin-bottom:2px}
.row{display:flex;justify-content:space-between;gap:12px;align-items:baseline;font-size:13.5px}
.row .a{color:var(--ink)}
.row .a.pick{color:var(--accent);font-family:"IBM Plex Mono",monospace;font-size:12.5px}
.row .v{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--muted);flex:none}
.gets{margin-top:12px}
.deltas{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px;padding-top:12px;border-top:1px solid var(--hair)}
.d-{font-family:"IBM Plex Mono",monospace;font-size:11px;padding:3px 7px;border-radius:2px;
  background:var(--sunken);color:var(--muted)}
.d- b{font-weight:600}
.d-.up b{color:var(--pos)} .d-.down b{color:var(--neg)}
.pitch{padding:16px 18px;background:var(--sunken);border-top:1px solid var(--line)}
.pitchhead{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.pitchhead span.lb{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint)}
.who{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}
button{font-family:"IBM Plex Mono",monospace;font-size:11px;border:1px solid var(--line);
  background:var(--surface);color:var(--body);padding:4px 10px;border-radius:2px;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button[aria-pressed="true"]{background:var(--accent);color:var(--surface);border-color:var(--accent)}
blockquote{margin:0;font-size:13.5px;color:var(--body);white-space:pre-wrap;
  border-left:2px solid var(--line);padding-left:14px;line-height:1.62}

.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:3px;padding:18px}
.panel h3{font-size:12px;font-family:"IBM Plex Mono",monospace;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);font-weight:500;margin-bottom:14px}
.kv{display:flex;justify-content:space-between;gap:14px;padding:7px 0;
  border-bottom:1px solid var(--hair);font-size:13.5px}
.kv:last-child{border-bottom:0}
.kv .k{color:var(--body)}
.kv .v{font-family:"IBM Plex Mono",monospace;color:var(--ink);
  font-variant-numeric:tabular-nums;flex:none}
.note{font-size:12.5px;color:var(--muted);margin-top:12px;line-height:1.55}

footer{margin-top:52px;padding-top:24px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}
footer b{color:var(--ink)}
.cmd{font-family:"IBM Plex Mono",monospace;font-size:12.5px;background:var(--sunken);
  border:1px solid var(--hair);padding:10px 12px;border-radius:2px;color:var(--ink);
  overflow-x:auto;white-space:pre;margin-top:12px}

@media (max-width:760px){
  .claims{grid-template-columns:1fr}
  .claim{border-right:0;border-bottom:1px solid var(--hair)}
  .claim:last-child{border-bottom:0}
  .sides,.rosters,.grid2{grid-template-columns:1fr}
  .side{border-right:0;border-bottom:1px solid var(--hair)}
  .side:last-child{border-bottom:0}
  header{padding-block:38px 26px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>

<div class="wrap">
<header>
  <p class="eyebrow" id="eyebrow">Dynasty fantasy football</p>
  <h1>It doesn't grade your&nbsp;trade. It finds&nbsp;it.</h1>
  <p class="lede">Every other tool answers <em>is this fair?</em> — a question about a trade you already imagined. This one reads every roster in the league, works out who is holding value they cannot deploy and who has a hole, and hands you the ranked list with the message to send.</p>
  <div class="detected" id="detected"></div>
  <div class="notice" id="notice"></div>
</header>

<section>
  <div class="shead"><h2>Nothing here is configured</h2><span class="tag">Detected from the league</span></div>
  <p class="sdek">Team count, PPR level, starting slots, flex eligibility and superflex are all read off the league's own payload. The two numbers that drive every decision below are measured from the league too, not set by hand.</p>
  <div class="claims" id="claims"></div>
</section>

<section>
  <div class="shead"><h2>The board</h2><span class="tag">Deployed vs held value</span></div>
  <p class="sdek">The diagnostic is the <b>gap</b> between where a roster's starting lineup ranks and where its total value ranks — never the raw number. Two teams can hold near-identical total value and be in opposite situations.</p>
  <div class="scroller">
    <table>
      <thead><tr>
        <th class="l">Team</th><th>Rec</th><th>Starters</th><th>Players</th><th>Picks</th>
        <th>Total</th><th>Ovr</th><th>St</th><th>Gap</th><th class="l">Window</th><th class="l">Shape</th>
      </tr></thead>
      <tbody id="board"></tbody>
    </table>
  </div>
  <div class="legend" id="legend"></div>
</section>

<section>
  <div class="shead"><h2>Every roster</h2><span class="tag">Optimal lineup, bench, taxi, picks</span></div>
  <p class="sdek">The lineup shown is the <b>best available</b> one, re-solved from the roster — not whichever lineup the manager happened to set. That is what makes a bench player's value legible: if he does not appear here, his market value is surplus, and surplus is what gets traded.</p>
  <div class="rosters" id="rosters"></div>
</section>

<section>
  <div class="shead"><h2>Trades that should happen</h2><span class="tag">Ranked, both sides willing</span></div>
  <p class="sdek">A proposal only exists if <b>each side improves relative to its own window</b>. A contender will overpay to raise its lineup; a rebuilder will not be fleeced. That asymmetry is why these clear at all — a fairness grader would call several of them lopsided.</p>
  <p class="sdek" style="margin-top:-12px">The order is deliberately diversified, so <b>score does not fall monotonically</b>: a slightly weaker deal that brings in new teams outranks a stronger variation on the one above it. Twelve managers share one league, and a list where the same pair takes every slot leaves the rest with nothing.</p>
  <div class="props" id="props"></div>
</section>

<section>
  <div class="shead"><h2>Why it's not hardcoded</h2><span class="tag">Format sensitivity is derived</span></div>
  <div class="grid2">
    <div class="panel">
      <h3>Positional demand, measured</h3>
      <div id="demand"></div>
      <p class="note">Dedicated slots are counted directly; flex demand is measured by looking at which positions actually win flex slots across every team's optimal lineup. Under full PPR that lands on receivers. Change the scoring to non-PPR and it moves to the backs on its own, with no code change and no table of positional multipliers anywhere in the system.</p>
    </div>
    <div class="panel">
      <h3>Picks priced on the player scale</h3>
      <div id="picks"></div>
      <p class="note">The source file ships pick <span class="mono">ECR</span> — a rank, not a value — so picks and players arrive incomparable. A monotone ECR&rarr;value curve fitted from the player board converts them. Labels also change by horizon: exact for the next draft, tier after that, round only beyond.</p>
    </div>
  </div>
</section>

<footer>
  <p><b>Free, non-commercial use.</b> Sleeper's public API is licensed for non-commercial use only, so this ships with every feature enabled and no paid tier until they say otherwise in writing. It reads only — no password, no OAuth, no writes to any league.</p>
  <p style="margin-top:14px">Values from <b>DynastyProcess</b> (Tan Ho, Joe Sydlowski). League data from the <b>Sleeper API</b>. Both credited wherever output is shown.</p>
  <div class="cmd">python3 -m src.cli report &lt;your-sleeper-username&gt;   # regenerates this page for your league
python3 -m src.cli doctor &lt;your-sleeper-username&gt;
python3 -m src.cli trades &lt;league_id&gt; --roster N</div>
</footer>
</div>

<script id="data" type="application/json">__REPORT_DATA__</script>
<script>
(function(){
  var D = JSON.parse(document.getElementById('data').textContent);
  var esc = function(s){ return String(s).replace(/[&<>"]/g, function(c){
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]; }); };
  var n = function(v){ return Number(v).toLocaleString('en-US'); };
  var sign = function(v){ return (v>0?'+':'') + n(v); };
  var ME = D.meta.focus;
  var byId = {}; D.teams.forEach(function(t){ byId[t.id] = t; });
  var mine = ME != null ? byId[ME] : null;

  document.getElementById('eyebrow').textContent =
    'Dynasty fantasy football · ' + D.league.name + ' · ' + D.league.season;

  var L = D.league;
  document.getElementById('detected').innerHTML = [
    ['Format', L.fmt],
    ['Starters', L.starters.length + ' — ' + L.starters.join(' ')],
    ['Bench / taxi / IR', L.bench + ' / ' + L.taxi + ' / ' + L.ir],
    ['Value coverage', D.coverage.toFixed(1) + '%']
  ].map(function(c){
    return '<span class="chip"><span class="k">'+esc(c[0])+'</span><b>'+esc(c[1])+'</b></span>';
  }).join('');

  document.getElementById('notice').innerHTML = D.meta.source === 'sample'
    ? '<b>Sample league.</b> Real players and real DynastyProcess values; the managers and rosters are invented. Point the same command at a real Sleeper league and this page regenerates from it — nothing below is written by hand.'
      + (mine ? ' The team standing in for yours is <b>' + esc(mine.name) + '</b>, highlighted throughout.' : '')
    : '<b>' + esc(L.name) + '</b>, generated ' + esc(D.meta.generated) + ' from live Sleeper and DynastyProcess data.'
      + (mine ? ' Your team is <b>' + esc(mine.name) + '</b>, highlighted throughout.' : '');

  var dm = D.demand, rp = D.replacement;
  var deepest = Object.keys(dm).sort(function(a,b){ return dm[b]-dm[a]; })[0];
  document.getElementById('claims').innerHTML = [
    ['<span class="num">'+dm[deepest].toFixed(2)+'</span>',
     'Starters at '+deepest+' this league demands, counting the share of flex slots '+deepest
     +' actually wins under this scoring. Nothing declares that — it is counted.'],
    ['<span class="num">'+n(rp.RB)+'</span>',
     'Replacement level at running back — the first back this league cannot start. It is what separates a tradeable asset from a body, and it is computed from these rosters.'],
    ['<span class="num">'+D.proposals.length+'</span>',
     'Proposals where both managers come out ahead in their own terms. Generated in well under a second, with no model call anywhere in the matching path.']
  ].map(function(c){
    return '<div class="claim"><div class="fig">'+c[0]+'</div><div class="lbl">'+c[1]+'</div></div>';
  }).join('');

  var shape = function(t){
    var out = [];
    Object.keys(t.surplus).forEach(function(k){ out.push('<span class="s">'+k+' +'+n(t.surplus[k])+'</span>'); });
    Object.keys(t.deficit).forEach(function(k){ out.push('<span class="d">'+k+' −'+n(t.deficit[k])+'</span>'); });
    return out.length ? out.join('  ') : '<span class="none">balanced</span>';
  };
  document.getElementById('board').innerHTML = D.teams.map(function(t){
    var g = t.gap>0?'p':(t.gap<0?'n':'z');
    return '<tr'+(t.id===ME?' class="me"':'')+'>'
      + '<td class="l"><div class="tname">'+esc(t.name)
      + (t.id===ME?'<span class="you">YOU</span>':'')+'</div>'
      + '<div class="tmgr">'+esc(t.manager)+'</div></td>'
      + '<td class="num rk">'+esc(t.record)+'</td>'
      + '<td class="num">'+n(t.starter)+'</td><td class="num">'+n(t.player)+'</td>'
      + '<td class="num">'+n(t.picks)+'</td><td class="num tot">'+n(t.total)+'</td>'
      + '<td class="num rk">'+t.ovr+'</td><td class="num rk">'+t.st+'</td>'
      + '<td class="num gap '+g+'">'+(t.gap>0?'+':'')+t.gap+'</td>'
      + '<td class="l"><span class="win" data-w="'+esc(t.window)+'">'+esc(t.window)+'</span></td>'
      + '<td class="l shape">'+shape(t)+'</td></tr>';
  }).join('');

  var post = {CONTENDER:'push','TOP-HEAVY':'mortgaged, no reload',RETOOLER:'consolidate',
              STUCK:'pick a direction',REBUILD:'hold'};
  var vars = {CONTENDER:'--w-contender','TOP-HEAVY':'--w-topheavy',RETOOLER:'--w-retooler',
              STUCK:'--w-stuck',REBUILD:'--w-rebuild'};
  document.getElementById('legend').innerHTML = Object.keys(post).map(function(k){
    return '<div><span class="dot" style="background:var('+vars[k]+')"></span>'
      + '<b class="mono" style="font-size:11.5px">'+esc(k)+'</b> — '+esc(post[k])+'</div>';
  }).join('');

  /* rosters -- the user's team first, then by overall rank */
  var pline = function(p, slot){
    if (!p) return '<div class="prow empty"><span class="slot">'+esc(slot||'')+'</span>'
      + '<span class="pn">empty</span><span class="pp"></span><span class="pv">—</span></div>';
    return '<div class="prow"><span class="slot">'+esc(slot||'')+'</span>'
      + '<span class="pn">'+esc(p.name)+'</span>'
      + '<span class="pp">'+esc(p.pos)+(p.age!=null?' '+p.age:'')+'</span>'
      + '<span class="pv">'+(p.unmatched?'—':n(p.value))+'</span></div>';
  };
  var groupBlock = function(label, list){
    if (!list || !list.length) return '';
    return '<div class="rgroup"><div class="rglabel">'+esc(label)+' · '+list.length+'</div>'
      + list.map(function(p){ return pline(p, ''); }).join('') + '</div>';
  };
  var order = D.teams.slice().sort(function(a,b){
    if (a.id===ME) return -1; if (b.id===ME) return 1; return a.ovr-b.ovr;
  });
  document.getElementById('rosters').innerHTML = order.map(function(t){
    var picks = t.pickList.map(function(p){
      return '<span class="pk'+(p.own?'':' traded')+'">'+esc(p.label)
        + ' <i>'+n(p.value)+'</i></span>';
    }).join('');
    return '<article class="rcard'+(t.id===ME?' me':'')+'">'
      + '<div class="rhead"><b>'+esc(t.name)+'</b>'
      + (t.id===ME?'<span class="you">YOU</span>':'')
      + '<span class="win" data-w="'+esc(t.window)+'">'+esc(t.window)+'</span>'
      + '<span class="rmeta">'+esc(t.manager)+' · '+esc(t.record)
      + ' · total <b>'+n(t.total)+'</b></span></div>'
      + '<div class="rbody">'
      + '<div class="rgroup"><div class="rglabel">Optimal lineup · '+n(t.starter)+'</div>'
      + t.lineup.map(function(s){ return pline(s.player, s.slot); }).join('') + '</div>'
      + groupBlock('Bench', t.bench) + groupBlock('Taxi', t.taxi) + groupBlock('IR', t.ir)
      + '<div class="rgroup"><div class="rglabel">Picks · '+n(t.picks)+'</div>'
      + '<div class="chips">'+picks+'</div></div>'
      + '</div></article>';
  }).join('');

  /* proposals */
  var assetRows = function(list){
    return list.map(function(a){
      return '<div class="row"><span class="a'+(a.pick?' pick':'')+'">'+esc(a.label)+'</span>'
        + '<span class="v">'+n(a.value)+'</span></div>';
    }).join('');
  };
  var delta = function(label, v, goodUp){
    if (!v) return '';
    var good = goodUp ? v>0 : v<0;
    return '<span class="d- '+(good?'up':'down')+'">'+label+' <b>'+sign(v)+'</b></span>';
  };
  document.getElementById('props').innerHTML = D.proposals.map(function(p, i){
    var involvesMe = p.sides.some(function(s){ return s.id===ME; });
    var start = 0;
    p.sides.forEach(function(s, k){ if (s.id===ME) start = k; });
    var sides = p.sides.map(function(s){
      return '<div class="side"><div class="sname"><b>'+esc(s.team)+'</b>'
        + (s.id===ME?'<span class="you">YOU</span>':'')
        + '<span class="win" data-w="'+esc(s.window)+'">'+esc(s.window)+'</span></div>'
        + '<div class="flow"><div class="hdr">Sends</div>'+assetRows(s.sends)+'</div>'
        + '<div class="flow gets"><div class="hdr">Gets</div>'+assetRows(s.gets)+'</div>'
        + '<div class="deltas">'+delta('lineup',s.lineup,true)+delta('market',s.market,true)
        + delta('capital',s.capital,true)+'</div></div>';
    }).join('');
    var who = p.sides.map(function(s, k){
      return '<button type="button" data-p="'+i+'" data-s="'+k+'" class="whoBtn" '
        + 'aria-pressed="'+(k===start)+'">'+esc(s.team)+'</button>';
    }).join('');
    return '<article class="prop'+(i===0?' top':'')+(involvesMe?' mine':'')+'">'
      + '<div class="phead"><span class="rank">'+(i+1)+'</span>'
      + '<span class="ptitle">'+p.sides.map(function(s){return esc(s.team);}).join(' &harr; ')+'</span>'
      + '<span class="pmeta"><span>score <b>'+n(p.score)+'</b></span>'
      + '<span>mutuality <b>'+p.mutuality.toFixed(2)+'</b></span></span></div>'
      + '<div class="sides">'+sides+'</div>'
      + '<div class="pitch"><div class="pitchhead"><span class="lb">Message to send, from</span>'
      + '<span class="who">'+who+'</span></div>'
      + '<blockquote id="pitch-'+i+'">'+esc(p.pitches[start])+'</blockquote>'
      + '<div style="margin-top:10px"><button type="button" class="copyBtn" data-p="'+i+'">Copy message</button></div>'
      + '</div></article>';
  }).join('');

  var shown = {};
  D.proposals.forEach(function(p,i){
    var s = 0; p.sides.forEach(function(x,k){ if (x.id===ME) s = k; });
    shown[i] = s;
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
      var i = +b.dataset.p;
      var text = D.proposals[i].pitches[shown[i]];
      var done = function(ok){ b.textContent = ok ? 'Copied' : 'Select & copy';
        setTimeout(function(){ b.textContent = 'Copy message'; }, 1800); };
      try { navigator.clipboard.writeText(text).then(function(){done(true);},function(){done(false);}); }
      catch(e){ done(false); }
    });
  });

  document.getElementById('demand').innerHTML = Object.keys(D.demand).map(function(k){
    return '<div class="kv"><span class="k">'+esc(k)+' starters per team</span>'
      + '<span class="v">'+D.demand[k].toFixed(2)+'</span></div>';
  }).join('') + Object.keys(D.replacement).map(function(k){
    return '<div class="kv"><span class="k">'+esc(k)+' replacement level</span>'
      + '<span class="v">'+n(D.replacement[k])+'</span></div>';
  }).join('');

  document.getElementById('picks').innerHTML = D.picks.map(function(p){
    return '<div class="kv"><span class="k mono" style="font-size:13px">'+esc(p.label)+'</span>'
      + '<span class="v">'+n(p.v)+'</span></div>';
  }).join('');
})();
</script>
"""
