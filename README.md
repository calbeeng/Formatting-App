# Notes to Google Docs

A small desktop app (Mac and Windows) that copies study notes from PDFs into a
Google Doc while keeping the structure: heading numbering like "(ii)", bullet
nesting, bold/italic/underline, superscripts, colour (blue = non-essential
readings stays blue), tables and diagrams. The formatting comes from *your*
Google Doc's own heading and text styles.

---

## Install it

Go to the **[download page](../../releases/latest)** and download the file for your computer:

| Your computer | File |
|---|---|
| Mac (Apple chip: M1, M2, M3, M4…) | `NotesToGoogleDocs-Mac-AppleChip.zip` |
| Windows | `NotesToGoogleDocs-Windows.exe` |

(Older Intel Macs aren't supported by the download; they can run it from source, see below.)

### Mac: first-time opening

The app isn't registered with Apple (that costs $99 a year), so macOS blocks it
the first time. This is expected and only happens once.

1. Double-click the downloaded `.zip`. A **Notes to Google Docs** app appears.
   Drag it into your **Applications** folder.
2. Double-click the app. macOS says it **"Not Opened"** / "Apple could not
   verify…". Click **Done** (not "Move to Bin").
3. Open **System Settings → Privacy & Security** and scroll down to the
   **Security** section. You'll see *"Notes to Google Docs" was blocked…*.
   Click **Open Anyway**, enter your Mac password, then click **Open Anyway** again.
4. The app opens. From now on it opens normally with a double-click.

**If there's no "Open Anyway" button**, or macOS says the app "is damaged",
open **Terminal** (Applications → Utilities), paste this line, press Return,
then open the app again:

```
xattr -dr com.apple.quarantine "/Applications/Notes to Google Docs.app"
```

### Windows: first-time opening

Windows may show **"Windows protected your PC"**. Click **More info → Run
anyway**. Keep the `.exe` somewhere permanent (e.g. Documents) and right-click
it → **Pin to Start** or **Send to → Desktop** for a shortcut.

### Signing in to Google (once)

1. Get the **client file** (`client_secret_….json`) from whoever set up the
   Google Cloud project (see [SETUP.md](SETUP.md)), and save it somewhere like Downloads.
   Your Gmail address must also be on that project's test-user list.
2. In the app, click **Sign in with Google** (top right). When asked, choose
   the client file.
3. Your browser opens. Choose your Google account. On **"Google hasn't
   verified this app"**, click **Continue** (or **Advanced → Go to notes2gdoc**),
   tick **all** the permission boxes, and click **Continue**.

---

## Using it

1. Click **Open file…**, or drag a PDF onto the window.
2. In the outline on the left, untick anything you don't want (unticking a
   heading unticks everything under it), or use **Pages … to …**. The preview
   on the right shows exactly what will be added.
3. Paste your Google Doc's link at the bottom, or pick one of your recent docs.
4. **Insert at:** the end of the document, or the end of a chosen heading's section.
5. Click **Append**.

**Settings…** lets you choose which Google Docs style each kind of heading uses
(e.g. "(a)" headings as Heading 3), and switch justified text and colour on or off.

Scanned PDFs (pictures of pages with no selectable text) aren't supported yet.

---

## For the maintainer

### Releasing a new version

GitHub builds the Mac and Windows apps automatically (see
`.github/workflows/build.yml`). To publish a new download:

```bash
git tag v1.0.1
```
```bash
git push origin v1.0.1
```

After about 10 minutes the new files appear on the download page. Every push to
`main` also builds and self-tests both versions (see the **Actions** tab).

### Run from source (fallback)

You need Python 3.11 or newer.

```bash
python -m venv ~/.venvs/notes2gdoc
```
Then install the app into it (Windows: `~\.venvs\notes2gdoc\Scripts\python`;
Mac: `~/.venvs/notes2gdoc/bin/python`), from this folder:
```bash
~/.venvs/notes2gdoc/bin/python -m pip install -e ".[dev]"
```
and start it with `~/.venvs/notes2gdoc/bin/notes2gdoc-app`. Keep the virtual
environment outside OneDrive/iCloud folders.

### Command line and tests

- `notes2gdoc outline FILE.pdf [--debug] [--plain] [--pages 2-5] [-o out.txt]` prints the extracted outline.
- `notes2gdoc append FILE.pdf DOC_LINK [--dry-run plan.json]` appends from the command line.
- `python -m pytest` runs the tests. Tests that need the course PDFs in
  `Samples/` skip themselves when those aren't present (they're never published).

### Tweaking the PDF rules

The heuristics live in `src/notes2gdoc/parsers/pdf/` and
`src/notes2gdoc/numbering.py`. Each file starts with a plain-English
explanation, and the tunable numbers are UPPER_CASE constants near the top.
