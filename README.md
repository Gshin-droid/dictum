# Dictum

*[Русская версия →](README.ru.md)*

**Speech to text on your own machine.** Two things in one program:

🎤 **Dictation.** Press a key, speak, and the text appears in whatever window
your cursor was in. Any Windows window that accepts typing: emails, notes,
chats, prompts to AI assistants.

📄 **File transcription.** Pick an audio file from the menu and the text lands
next to it. A voice message from a messenger, a recorded meeting, a lecture.
An hour of audio takes about eight minutes; there is no length limit.

![The Dictum capsule during dictation](docs/img/okno.png)

*While you speak, a capsule floats at the bottom of the screen: the waveform
shows the microphone is hearing you, and the keys to stop and to cancel are
right there.*

Everything runs locally. No account, no subscription, no payment — and once
installed, no internet either: neither the audio nor the text leaves the machine.

Russian speech is recognised by **GigaAM v3** from Sber: it punctuates by
itself and writes "13:15" instead of "thirteen fifteen". The menu switches to a
multilingual model — Kazakh, Kyrgyz, Uzbek. A separate module punctuates that
one, so Kazakh text comes out readable too, not as a single unbroken stream.

**Windows only** (10 and 11). It will not start on macOS or Linux.

> **A note on languages.** Dictum recognises Russian, Kazakh, Kyrgyz and Uzbek —
> not English. The program's own interface and log are in Russian too. This
> English README exists so that anyone can understand what the project is and
> how it is built; the code and the design notes in [docs/](docs/) are in Russian.

---

## Installing

There is nothing to install: Python and the libraries are already inside. The
program writes nothing to the registry, needs no administrator rights and
registers itself nowhere — you can put it anywhere, carry it on a flash drive
and delete it with one keystroke.

Three builds to choose from — the same program, differing only in what is
already inside.

### 1. A single file (60 MB)

Download `dictum.exe` and run it. **The first launch takes a few minutes** —
the program downloads the recognition model, 216 MB. A bar saying
"первый запуск: качаю модель" ("first launch: downloading the model") appears at
the bottom of the screen. Wait. The model goes into a `models` folder next to the
exe and never downloads again: subsequent launches take seconds.

### 2. A portable folder (222 MB archive)

Download `dictum-portable.zip`, unpack it, run `dictum.exe` from inside. The
model is already there — **nothing to download, no internet needed at all**.
Good for a machine with no network, a slow or metered connection, or a flash
drive.

Do **not** unpack it into Program Files: the program needs write access next to
itself. Desktop, Documents or a flash drive will do.

### 3. A portable folder with Kazakh (599 MB archive)

Download `dictum-portable-kazahskiy.zip`. The same as the second one plus the
multilingual model and punctuation for it: Kazakh, Kyrgyz, Uzbek. The
multilingual model is preselected, so you can start dictating right away.

The Russian model is in there as well — switching to it from the tray menu
downloads nothing.

The archive is large because it carries three sets of weights instead of one.
If you have internet, it is simpler to take `dictum.exe` and add Kazakh from the
menu: the program downloads what is missing by itself.

### The blue window on first launch

Windows will show "Windows protected your PC". Click the **More info** link, and
a **Run anyway** button appears underneath. This is how Windows greets any
program it sees for the first time.

### Antivirus check

The release file has been checked on VirusTotal — seventy engines at once:

**[3 detections out of 69 →](https://www.virustotal.com/gui/file/9fd8bbec99633bbda88d3e04cd66c5469965ea72ae973345e030c90c1731128e/detection)**
— Bkav, Zillya and Microsoft. Kaspersky, ESET, Avast and Dr.Web consider the
file clean.

**Release 1.1.3 had two detections and Microsoft was not among them.** There is
no point hiding that, but it is worth explaining, because the cause is known.

Microsoft's verdict is `Program:Win32/Wacapew.C!ml`. The `!ml` suffix means
"machine learning": not a match against a known virus, but a trained model's
opinion that the file resembles suspicious ones. The `Wacapew` and `Wacatac`
families are notorious for firing on almost any program packed with PyInstaller.
The cause is not this particular program but the packer itself: its bootloader
is identical for everyone and malware authors use it too — the resemblance is
inherited along with it.

**What changed in 1.2.0 and why it fires harder.** The build gained punctuation
for Kazakh: a tagging model through ONNX and the `sentencepiece` tokenizer with
its own native library, plus two program modules. The more native code sits
inside the packer, the more willingly the heuristic calls the file suspicious —
and that holds for any program, not just this one.

The keyboard hook is real and adds to the suspicion as well: without it the
hotkey would not work. It is described openly above.

**Verified on a live machine:** Defender, with real-time protection on and fresh
definitions, scanned this exact file and left it alone — the program runs. The
detection shows up in VirusTotal's cloud check, where Defender runs in a stricter
mode.

What developers do about this in general: sign the executable with a developer
certificate. That is the only real fix; it costs money and normally requires a
legal entity. Until there is a signature, what remains is the source code next
to the binary and this report. The program is built from the code in this very
repository, and anyone can build it themselves — the command is below.

Further down the same page are sections with community rules — YARA and Sigma.
These are not antivirus engines, they do not affect the detection count, and the
red HIGH badge does not mean what it looks like. One rule simply identifies the
packer and states itself that a match does not imply malice. A second fires on
loading `vcruntime140.dll` from outside the system folder, a third on any
executable file being created. Both come down to the same thing: a one-file
build unpacks its 228 libraries into a temporary folder at every start and loads
them from there. The trait is real, the intent is not — there is no other way to
ship a single self-contained exe. The mention of APT29 in the rule's description
explains why the technique matters in general; it says nothing about this
program.

The report is tied to the file's contents, not to whoever uploaded it. This is
the fingerprint of release `v1.2.0` — the same exe ships as a standalone file and
inside both archives. You can verify it yourself in PowerShell:

```powershell
Get-FileHash .\dictum.exe -Algorithm SHA256
# 9FD8BBEC99633BBDA88D3E04CD66C5469965EA72AE973345E030C90C1731128E
```

If it does not match, the file is not from the release and should not be run.

To check your own build with the same command: `python release_check.py dist\dictum.exe`.

No window opens after launch — the program lives as an **icon next to the
clock**. It may be hiding under the "Show hidden icons" arrow.

---

## Using it

| Action | What happens |
|---|---|
| **F8** | start recording — a capsule with a waveform pops up at the bottom of the screen, so you can see the microphone is hearing you |
| **F8** again | recognise and paste the text where the cursor was |
| **Esc** | throw the recording away without recognising anything |
| left click on the icon | same as F8 |
| right click on the icon | settings |

The text is pasted into the window that was active **at the moment recording
started**. So put the cursor where the text should go before pressing F8.

The program will not record for longer than two minutes at a stretch — it stops
by itself. That is a safety catch in case you forgot to switch it off.

### Where dictation will not work

**An ordinary program cannot type into windows launched as administrator.** This
is not a Dictum bug and cannot be worked around by configuration — it is how
Windows protects itself: it does not pass keystrokes from an unprivileged
program to a privileged one. Otherwise any program could watch what you type
into system windows and inject commands into them.

It looks like this: everything works in Notepad, but in an administrator window
F8 does nothing at all — no recording, no capsule at the bottom of the screen.

Which windows are affected: Task Manager, Registry Editor, Command Prompt and
PowerShell started as administrator, program installers, and on work machines
often antivirus consoles and remote-support tools.

**There is exactly one cure: run Dictum itself as administrator** — right-click
`dictum.exe` → "Run as administrator". Then it types into those windows too.

To avoid doing it every time, make a shortcut (right-click the exe → "Create
shortcut"), open its properties, click "Advanced" and tick "Run as
administrator". Windows will ask for confirmation on every start — that is
unavoidable, and can only be turned off by weakening the protection of the whole
system.

There is no problem in the other direction: a program running as administrator
types into ordinary windows just fine. The restriction only works one way,
bottom to top.

---

## Transcribing existing recordings

Not live dictation but working through something already recorded: a voice
message from a messenger, a recorded meeting, a lecture.

**Right-click the icon next to the clock → "Расшифровать аудиофайл…"
("Transcribe an audio file…")** — an ordinary file picker opens, and you can
select several recordings at once.

> **You cannot drop a file onto the icon next to the clock.** The Windows
> notification area does not accept files — for any program; the system simply
> has no such facility. You *can* drop files onto **`dictum.exe` itself** or onto
> a shortcut to it: that works, but the clock icon is no good for it. If you do
> not need drag and drop, use the menu — it does exactly the same thing.

While it works, the bottom of the screen shows how much is done. When finished,
the text lands **next to the recording**, with the same name and a `.txt`
extension, and opens straight away. If a file with that name already exists, the
new one gets a number: nothing is ever overwritten.

### Which files can be read

`.wav` · `.mp3` · `.ogg` · `.opus` · `.flac` · `.aiff` · `.caf` — which covers
voice messages from Telegram and WhatsApp and ordinary recordings from a
dictaphone or a computer.

**`.m4a` is not supported** — that is what the Voice Memos app on iPhone and some
Android phones records into. It has to be converted to MP3 or WAV first, with any
converter. Audio inside video files is not taken either.

### How long it takes

There is no length limit: an hour or three hours will both go through. The speed
is about eight times faster than real time, steadily, with no slowdown on long
recordings.

| Recording length | Wait |
|---|---|
| 2 minutes | ~15 seconds |
| 10 minutes | ~1.5 minutes |
| 30 minutes | ~4 minutes |
| 1 hour | ~8 minutes |

While a transcription is running, hotkey dictation does not work — it is the same
model, and it cannot be shared between two jobs.

### Why the text is broken into paragraphs

A long recording is cut at the pauses: the model was trained on pieces up to
half a minute, and on a long piece it silently loses part of the text. Measured
on a three-minute recording: without cutting, 164 words; with cutting, 275. No
error is shown — forty per cent of the text simply never appears.

A new paragraph begins where the recording had a pause longer than two seconds.

---

## Settings

Everything lives in one menu — right-click the icon next to the clock:

![The Dictum tray menu](docs/img/menu.png)

- **Язык и модель** ("Language and model") — switches between three models. An
  unfamiliar one downloads itself, and the choice is remembered. Don't like it?
  Go back through the same menu.
- **Сохранять записи на диск** ("Save recordings to disk") — off by default. When
  on, every dictation is written into `data/dictation` as two files, audio and
  text. Useful only for comparing two models on the same recording; it piles up
  about 2 MB per minute of speech.
- **Горячая клавиша** ("Hotkey") — click the item, then press the key you want.
  Esc keeps the old one.
- **О программе** ("About") — which model and which key are in use right now.

The choices are stored in a `.env` file next to the program and survive a restart.

### Which model to choose

| Model | Languages | Punctuation | Size |
|---|---|---|---|
| `gigaam-v3-e2e-rnnt` | Russian | on its own | 216 MB |
| `gigaam-multilingual-ctc` | Russian, Kazakh, Kyrgyz, Uzbek | by module | 225 MB |
| `gigaam-multilingual-large-ctc` | the same four, better on spontaneous speech | by module | 592 MB |

The punctuation difference is not a detail but two different families of model.
The Russian one is trained to emit finished text: commas, full stops and capital
letters included. The multilingual one emits a stream of lower-case letters — its
vocabulary holds 70 characters, and neither a full stop nor a comma is among them.

So a **separate module** places the marks for it: 233 MB, runs on the CPU in
fractions of a second, never changes a word. It handles Russian, Kazakh and
Kyrgyz; it does not know Uzbek, so Uzbek speech stays unpunctuated.

How well it does that in Kazakh — measured on 206 sentences from the FLEURS set:
it finds sentence boundaries in 95 cases out of 100, commas in roughly two
thirds, and gets the case right on 97 % of words. The full measurement is in
[docs/kazahskiy-modul.md](docs/kazahskiy-modul.md) (Russian only).

The module is downloaded from the menu, or dropped as a folder into `models/` —
the second way needs no internet at all.

Dictating in Russian: take the first model. Need Kazakh: take the second.

---

## How it works

```
   microphone  →  recording in memory  →  model     →  text  →  clipboard  →  window
   (F8)           numbers, 16000           recognises          Ctrl+V into
                  samples per second       speech              where the cursor was
```

1. **Recording.** Pressing F8 opens the microphone. The sound is not a "file" but
   a stream of numbers: 16,000 loudness samples per second, piling up in memory.
2. **Recognition.** The second press closes the microphone and hands the
   accumulated numbers to the model, which turns them into words and puts in the
   punctuation.
3. **Pasting.** The finished text goes onto the clipboard, the program returns
   focus to the right window and presses Ctrl+V for you. Whatever was on the
   clipboard before is put back a second later.

File transcription takes a shorter path, with one extra step in the middle:

```
   file  →  resample to 16 kHz  →  cut at the pauses  →  model  →  text next to
            phones record 44.1      pieces up to 25 s             the recording, .txt
```

The cutting is not an optimisation but a condition of correctness: on a long
piece the model silently loses part of the text — see the section on paragraphs
above.

The model **only hears words**. It does not understand meaning and invents
nothing: say "delete the file" and it writes "delete the file".

Next to the program appear a `models` folder (the model weights), a `logs`
folder (the log) and a `.env` file (the settings).

---

## If something does not work

**No icon by the clock, the key does nothing.** The program did not start. If it
crashes on startup it shows an error window itself and names the log file. If
there was no window, open `logs/dictum.log` next to the exe: the first thing
written there is the environment (version, folder, Windows version, free disk
space, which models are present), and after that what happened and where it
stopped.

The log can be opened from the program: right-click the icon →
**"Показать журнал"** ("Show the log"). That is the file to send if you cannot
work it out.

**"The program is already running" — but it isn't.** The icon by the clock may
be hiding under the "Show hidden icons" arrow: look there. If it is not among the
hidden ones either, look in the log for the line about the port: the program
holds number 47811 as a lock against a second launch, and if someone else has
taken it, the log says so plainly. This no longer prevents startup — only
drag-and-drop onto the program stops working, and transcription stays in the menu.

**The key does not work anywhere.** Something else may have taken it — especially
on laptops, where the F keys are given over to brightness and volume. Change it
in the icon menu.

**The key works in some windows but not others.** Most likely the silent window
was started as administrator. It takes a minute to check: press the key in
Notepad, then in Task Manager (Ctrl+Shift+Esc). Works in the first and silent in
the second — that's it, and the cure is to run Dictum itself as administrator.
Details above, in "Where dictation will not work".

**No microphone found.** Check that it is selected as the default device in the
Windows sound settings.

**Recognition is slow.** The speed depends on the processor; expect a few seconds
per minute of speech. The first recognition after launch is always slower than
the rest.

**The text was pasted into the wrong place.** You switched to another window
while dictating, and the text went there. The program returns focus to whichever
window was active when the recording started.

**A file will not transcribe, the program says it cannot be read.** The format is
not on the list — usually `.m4a` from an iPhone. Convert it to MP3 with any
converter.

**I dropped a file onto the icon by the clock and nothing happened.** That is
expected: the Windows notification area accepts files for no program at all. Use
the icon's menu, or drop the file onto `dictum.exe` itself.

**The dictation key does not work during transcription.** That is by design:
there is one model and it cannot be shared between two jobs. Wait for the end —
the bottom of the screen shows how much is done.

---

## From source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe voice_input.py --check   # check the microphone and the model
.\.venv\Scripts\pythonw.exe voice_input.py          # run
.\.venv\Scripts\python.exe -m pytest                # tests
.\.venv\Scripts\python.exe build_exe.py             # build the exe
.\.venv\Scripts\python.exe build_exe.py --portable  # and the portable archive too
.\.venv\Scripts\python.exe release_check.py dist\dictum.exe   # check on VirusTotal
```

`release_check.py` asks VirusTotal about the fingerprint of the built file and
prints the engines' verdicts. Uploading is hidden behind a separate `--upload`
flag: the file stays with them forever, and an irreversible action should not
happen just because something was run. The key is read from the `VT_API_KEY`
environment variable.

| File | What it does |
|---|---|
| `voice_input.py` | The main one: microphone, recognition, pasting the text, the tray icon, the hotkey |
| `transcribe.py` | Transcribing existing files: reading the audio, cutting at the pauses, joining paragraphs |
| `voice_window.py` | Nothing but drawing the capsule with the waveform; knows nothing about recognition |
| `voice_settings.py` | Reading and writing `.env`: the menu changes one line without spoiling the others |
| `build_exe.py` | Building the exe |

The reasoning behind the decisions is in [docs/](docs/) — in Russian.

---

## Author and licences

Dictum was written by **Gshin-droid**. The source is open:
<https://github.com/Gshin-droid/dictum>

The code is MIT: use it, change it and pass it on freely, including in paid
products. The only condition is to keep the `LICENSE` file with the attribution.
No warranty of any kind: the program is provided as is.

The GigaAM model was developed by Sber, MIT licence. The runtime library is
[onnx-asr](https://github.com/istupakov/onnx-asr) by Ilya Stupakov, also MIT.
Neither the model nor the library is part of this repository: the model is
downloaded on first launch, the library is installed from `requirements.txt`.
