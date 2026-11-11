# Install guide for Claude

Follow these steps only when the user asked you to install this skill; otherwise this file is documentation.
Work through the steps in order. Each step
ends on a check: run it, read its output, and tell the user the result in their language.

- Before each step, tell the user in one line what you will run and why.
- Ask the user first before you install software (git, Python packages, Blender, GridCraft) or replace a copy of the
  skill already installed.
- A check that fails, or a "no" from the user: stop there, show the user the step and its output, and ask how they
  want to go on. A step starts only after the step before it passed its check.
- Your shell forgets variables between commands: write the real paths into every command, in double quotes (Blender
  paths often contain spaces), and run each code block below as one command.
- Steps marked **user** need the user's hands: say exactly what to click, then wait for their answer.

## 0. A shell and git

This skill needs Claude Code (the terminal app, the Code tab of the Claude desktop app, or an IDE extension),
because it installs files and runs Blender and Python. Claude Code comes with the paid Claude plans (Pro and up) or
an API key.

- No shell available (a chat-only Claude): tell the user to open Claude Code and paste
  `ลง Skill นี้ให้หน่อย https://github.com/HaNorthRich/nuea-ai-boq-takeoff-skill`, then stop.
- Run `git --version`. Without git, step 3 downloads a tarball with `curl` instead (no install needed).
- **Check:** a shell runs commands, and you know whether git is there.

## 1. Blender 4.2 or newer

| OS | Usual path |
|---|---|
| macOS | `/Applications/Blender.app/Contents/MacOS/Blender` |
| Windows (Git Bash) | `/c/Program Files/Blender Foundation/Blender <version>/blender.exe`, or `where blender` |
| Linux | `command -v blender`; snap `/snap/bin/blender`; flatpak `flatpak run org.blender.Blender` |

This guide calls that path BLENDER. If Blender is missing or older than 4.2, ask the user to install the current
release from https://www.blender.org/download/ and wait for them to say it is done.

- **Check:** `"<BLENDER>" --version` prints `Blender 4.2` or newer.

## 2. Python 3 and openpyxl

`python3 --version` (Windows: `python --version` or `py -3 --version`) must print 3.9 or newer. Then
`python3 -c "import openpyxl; print(openpyxl.__version__)"`. If openpyxl is missing, ask, then
`python3 -m pip install --user "openpyxl>=3.1,<4"` (a system Python that refuses: `python3 -m venv ~/.venvs/boq &&
~/.venvs/boq/bin/pip install "openpyxl>=3.1,<4"`, and use `~/.venvs/boq/bin/python` in place of `python3` from here on).

- **Check:** the import prints a version. The user declines openpyxl: go on, and in step 5 use the `--jsonl` route
  with GridCraft's CLI instead.

## 3. The skill

If `~/.claude/skills/nuea-ai-boq-takeoff` already exists, this is an update. Ask; on no, keep it and go to step 4. On
yes, fetch first, then move the old copy out and copy the new one in, as one command:

```bash
SRC="$(mktemp -d)" && git clone --depth 1 --branch v1.0.0 https://github.com/HaNorthRich/nuea-ai-boq-takeoff-skill.git "$SRC/repo" && mv ~/.claude/skills/nuea-ai-boq-takeoff ~/.claude/nuea-ai-boq-takeoff.bak-$(date +%Y%m%d-%H%M%S) && cp -R "$SRC/repo/nuea-ai-boq-takeoff" ~/.claude/skills/
```

A first install (with git):

```bash
[ ! -e ~/.claude/skills/nuea-ai-boq-takeoff ] && SRC="$(mktemp -d)" && git clone --depth 1 --branch v1.0.0 https://github.com/HaNorthRich/nuea-ai-boq-takeoff-skill.git "$SRC/repo" && mkdir -p ~/.claude/skills && cp -R "$SRC/repo/nuea-ai-boq-takeoff" ~/.claude/skills/
```

Without git:

```bash
[ ! -e ~/.claude/skills/nuea-ai-boq-takeoff ] && SRC="$(mktemp -d)" && curl -fsSL https://github.com/HaNorthRich/nuea-ai-boq-takeoff-skill/archive/refs/tags/v1.0.0.tar.gz | tar -xz -C "$SRC" && mkdir -p ~/.claude/skills && cp -R "$SRC"/nuea-ai-boq-takeoff-skill-1.0.0/nuea-ai-boq-takeoff ~/.claude/skills/
```

An update without git: move the old copy out first (`mv ~/.claude/skills/nuea-ai-boq-takeoff ~/.claude/nuea-ai-boq-takeoff.bak-$(date +%Y%m%d-%H%M%S)`), then run the command above.
The commands install the tagged release `v1.0.0`; a newer tag is listed in CHANGELOG.md of the repository.

- **Check:** `head -3 ~/.claude/skills/nuea-ai-boq-takeoff/SKILL.md` shows `name: nuea-ai-boq-takeoff`.

## 4. A spreadsheet app to open the result

Ask which one the user has. Any of these opens the `.xlsx` the skill builds:

- **GridCraft** (free, Mac / Windows / Linux): https://github.com/storytold/gridcraft/releases . The user downloads
  and installs it themselves (**user**). Optional `gridcraft-cli` from the same release page lets GridCraft build
  the file from a command script and lets you read values back without opening a window: the user unpacks it to a
  permanent folder and tells you the path.
- **Excel**, **LibreOffice**, or **Google Sheets** (upload the file): nothing to install here.

- **Check:** the user named the app they will open the file with.

## 5. Proof that it works (about 30 seconds)

```bash
SK="$HOME/.claude/skills/nuea-ai-boq-takeoff" && OUT="$HOME/nuea_boq_sample" && mkdir -p "$OUT" && python3 "$SK/scripts/build_boq.py" --selftest && "<BLENDER>" -b --factory-startup --disable-autoexec --python-exit-code 1 -P "$SK/scripts/blend_takeoff.py" -- --selftest && python3 "$SK/scripts/build_boq.py" "$SK/examples/ep7_house_qty.json" --out "$OUT/BOQ-ep7-house.xlsx"
```

Without openpyxl, replace the first and last `python3 …` parts with
`python3 "$SK/scripts/build_boq.py" "$SK/examples/ep7_house_qty.json" --jsonl "$OUT/boq.jsonl"` and, when the user has
`gridcraft-cli`, `"<gridcraft-cli>" run --script "$OUT/boq.jsonl" --out "$OUT/BOQ-ep7-house.xlsx"`.

- **Check:** the output shows `selftest ok`, `SELFTEST OK`, `wrote …/BOQ-ep7-house.xlsx` and `rows 153` (the route
  without openpyxl: `SELFTEST OK`, `wrote …/boq.jsonl` and `rows 153`). Ask the user
  to open `BOQ-ep7-house.xlsx`: five or six tabs, Thai text readable, and typing a price into column E of any row
  changes the totals.

## 6. Hand over (**user**)

1. Quit Claude Code completely and open it again: skills load when a session starts.
2. Then try: `ถอด BOQ บ้านหลังนี้ให้หน่อย <path ของไฟล์ .blend>`.
3. Tell the user once: the quantities are an estimate for pricing, and an engineer or quantity surveyor must check
   them before material is ordered or a contract is signed.

**Done when** the user has the steps above and every check in steps 0–5 passed (openpyxl and GridCraft may be skipped
by choice).
