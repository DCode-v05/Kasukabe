# Team Box Guide

A shared research computer for the team: **32 CPUs, 64 GB RAM, no GPU.** It stays off until someone starts it and
turns itself off when the work is done. You don't need any account. Everything goes through the **start link** the
project owner sends you privately. Don't post the link anywhere public.

## First time only

**1. Make an SSH key.** In a terminal on your computer (Mac, Linux, or Windows PowerShell), press Enter at every question:

```bash
ssh-keygen -t ed25519
```

If you already have a key, skip that. Then print the **public** half and copy the one line it shows:

```bash
cat ~/.ssh/id_ed25519.pub
```

Only ever share the file ending in `.pub`.

**2. Add it on the start page.** Open the start link, paste the line into **Add your SSH key**, and press **Add my key**.

## Every time

1. **Start it.** Open the start link and press **Start the box**. The page refreshes itself, and after about a minute
   it shows the exact command to connect, for example `ssh team@13.233.40.12`.
2. **Connect.** Paste that command into your terminal. The address changes every time the box starts, so copy it from
   the page each time. At a new address SSH asks whether to continue: type `yes`.
3. **Work inside tmux**, so jobs keep running when you disconnect:
   ```bash
   tmux new -s yourname
   ```
   Leave with `Ctrl-b` then `d`. Come back with `tmux attach -t yourname`.
4. **Save outputs in `~/results`.** Files stay on the box between sessions. To copy them to your computer, run this
   **on your computer**, using the address from the start page:
   ```bash
   scp -r team@ADDRESS:results ./team-box-results
   ```
5. **Close everything when you're done:**
   ```bash
   tmux kill-session -t yourname && exit
   ```
   An open tmux session counts as work, even an idle one, and keeps the box on, spending everyone's budget.

## Running experiments

Python and PyTorch are ready, and the research code is in `~/work`:

```bash
cd ~/work
python spread_store.py --seeds 0 --threads 4 --outdir ~/results/q6_seed0
```

- **Share the 32 CPUs.** Experiments use 4 threads each, so about 8 can run at once across the whole team. Check with
  `uptime`: a load above 28 means the box is full.
- **Save as you go.** The 4-hour limit stops everything, mid-job or not. Write results to disk as each part finishes.
- **Packages** go into the shared environment, so tell the team when you add one:
  `uv pip install --python /opt/venv/bin/python PACKAGE`. There is no `sudo`.

## When it turns itself off

| Rule | Value | What happens |
|---|---|---|
| Idle | 15 minutes | No job running and nobody connected: it turns off. |
| Per start | 4 hours | It turns off even mid-job. Press Start again to continue. |
| Team budget | ~10.5 hours total, shared, for good | 30 minutes before the end, connected terminals get a warning. At zero it turns off and won't start again. |

The start page always shows how much of the team's budget is left.

## If something looks wrong

| You see | It means | Do this |
|---|---|---|
| "Budget used up" | The team's hours are spent. | Ask the project owner. |
| `Permission denied (publickey)` | Your key isn't on the box, or you used a different key. | Add your `.pub` line on the start page, wait a minute, retry. |
| `Connection timed out` or `refused` | The box is off or starting, or you used an old address. | Reload the start page and use the address it shows now. |
| It turned off during a job | The 4-hour limit, or the budget ran out. | Start it again and resume from your saved results. |
| It turned off while I was away | No job ran for 15 minutes. | Nothing: it was saving the budget. |

Questions about access or the budget go to the project owner.
