# Code audit: extending MATCHANALYSIS into an evidence-first tactical decoder

## Overview

The original `PostMatchDashboard.py` is a large notebook export with global
state and plotting logic. `app.py` uploads WhoScored saved HTML and launches
that script as a subprocess, then combines its PNGs into PDF pages.

The new `tactic_decoder.py` is a separate, testable path. It directly parses
embedded match data, validates passing relationships, measures attacking
corridors and records formation changes. It has a CLI and an independent panel
in `app.py`. The old visual report has not been rewritten; see below.

## Verified in the supplied Como–RBL HTML (WhoScored match ID 2029236)

- Match metadata: Como 4–1 RBL; date 2026-09-10 (as stored by provider).
- 1,585 events, 1,081 pass events across both teams, 28 shot events.
- Como registered formation `4231` from 0–67', `3421` from 67–94'.
- Como passes: 545 attempts, 485 complete, 89.0% accuracy.
- Revised geometric progressive completed live-ball passes: 86. This differs
  from the old script's 36 because the old script sets progression to 0 for
  starting x <= 42m (and may include corner/restart categorization differences).
- Como completed half-space entry passes: 38; 28 final-third entries, 8
  penalty-area entries using the new decoder's coordinate definitions.
- Team receiver linking is inferred by a <=12-second time window, <=15m
  endpoint-to-next-control distance, same team and period, and no earlier
  opponent control event. It is a quality-filtered *hypothesis*, not ground truth.

## Critical problems identified in the original code

1. **False pass receiver links:** `get_passes_df()` assigns `playerId.shift(-1)`
   across all events, not successful pass targets. Opponent activity, fouls,
   intervening events and stoppages contaminate passing networks.
2. **Undirected passing graph:** `get_passes_between_df()` merges A→B and B→A
   using the minimum/maximum player ID, hiding tactical direction.
3. **Full-match averaging:** Pass network and defensive-actions averages pool
   starters, substitutes and different formations. These are not phase-specific.
4. **Defensive-action location mistaken for shape:** `defensive_block()` uses
   KDE of defensive actions and mean player event positions. It does not
   identify the off-ball defensive line or pressure triggers.
5. **xT concerns:** External grid fetched at runtime, coordinate bins computed
   with `pd.cut`, missing bin checks, plus a momentum plot based on *mean
   starting-zone xT*, rather than xT gained. The chart is not a validated
   measure of chance creation.
6. **Progressive-pass filter:** `pro` uses x>42m, excluding valid progression
   from deeper areas by construction. `draw_progressive_pass_map` divides by
   zero if no qualifying progressive passes are found.
7. **Shot statistic bug:** `axGpSh = axg/hTotalShots` should use away shots;
   zero-shot games may also divide by zero.
8. **Invented metadata:** league, matchweek and match date are hardcoded
   as `LaLiga, 2023-24`, `32`, and `19 April, 2024` even for the supplied
   2026/27 Champions League HTML.
9. **Unverified xG defaults:** Both the script and Streamlit app use arbitrary
   default xG/xGOT values; selecting just one team sets the other values to 0.
   Do not present these as measured match statistics.
10. **Possession vs pass-share:** Original `hposs`/`aposs` are percentages of
    pass attempts, which is not actual time-in-possession.
11. **Misleading sequence links:** The original shot build-up counts use
    shifted qualifier strings, which can connect unrelated events or teams.
12. **Repeated low-value sorting:** `nsmallest(10, 'total')` in the top-player
    bar charts selects the lowest totals, not the highest.
13. **Cross-match side bug:** The UI currently selects `home` or `away` once
    and reuses the same side across every match. This can scout the wrong club
    when the target changes home/away side. The new decoder selects a team by
    **name in each match**, not by fixed side.
14. **Old README mismatch:** The original documentation describes a notebook
    file that is not present in the supplied archive, which contains `.py`.

## Evidence types and what should not be claimed

**Directly recorded:** Initial and changed registered formations, event types,
minute, pass endpoints, outcomes, qualifiers, player IDs, substitutions.

**Estimated from events:** Receiver links, possession segments, shot sequences,
high-recovery-to-shot sequences, progression and pass entry corridors.

**Not identifiable without video/tracking:** precise 2-3-5/3-2-5 in-possession
shape, actual off-ball compactness, pressing triggers, man-marking, numerical
overloads as simultaneous player counts, fullback inverted positioning,
third-man off-ball running and rest-defence structure.

Never present a labelled formation inferred from one averaged pass map as
confirmed. Show the source, formula, sample, phase, and uncertainty instead.

## Recommended development order

1. Use the new decoder's normalized event structure and validation rules as
   the data layer for all charts. Avoid parsing dicts through CSV string casts.
2. Add phase-filtered passing networks (formation interval, game state,
   first/second half); render directed edges and display link coverage.
3. Add annotated goal/shot sequence views with pass chains and start/end
   locations. Cross-check a sample of links against video.
4. Validate phase-detection rules and positional bins against several known
   matches, especially stoppages, extra time, own goals and heavy substitutions.
5. Add independent xG/xT only when values/models are sourced and versioned;
   do not use fallback constants.
6. With tracking/video, add real dynamic attacking and defensive formations,
   PPDA/pressing intensity with appropriate event definitions, and true
   numerical overload detection.

## Command

```bash
python tactic_decoder.py "Como 4-1 RB Leipzig - Champions League 2026_2027 Live.html" --team Como --output como_tactics.json --markdown como_tactics.md
python -m unittest discover -s tests -v
streamlit run app.py
```

**Scope note:** Only the Leipzig HTML was provided. The four-match PDF is a
visual output, not a replacement for the other three match HTML sources. The
new engine supports multi-match analysis when those source files are supplied.
