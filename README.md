# OmaDex

One address layer for Omarchy. OmaDex reads the address books you already
have — your iPhone, abook, Evolution, neomutt aliases, indexed mail, CardDAV —
merges them into one set of people, and puts that behind a keyboard-driven
overlay.

It stores no contacts of its own. Everything it shows belongs to a source, and
every merged person can tell you which source each detail came from.

## What it does

- **Search everything at once.** `SUPER+CTRL+ALT+C`, type, Enter.
- **Show where a contact came from.** Expand a person to see the record each
  source contributed, then click one to open that application — neomutt
  composing to them, the exact vCard file, abook on the right address book.
- **Explain its merges.** A shared email address merges two records. A shared
  phone merges them only when the names also agree. Everything else is held
  for you to decide, and your decision survives every later sync.
- **Copy anything.** Click an address or a street address to put it on the
  clipboard.

## Requirements

Omarchy, for the overlay and its launch helpers. The CLI works anywhere.

Every source is optional and OmaDex runs with any subset of them. Install
nothing and the overlay tells you what to set up.

| Source | Needs | Preloads a contact when opened |
|---|---|---|
| abook | `abook` | no — abook cannot open at a record |
| iPhone | `blueferry-backend` **from the fork** | no — no thread selector exists |
| Evolution | `evolution-data-server` | needs `gnome-contacts` |
| neomutt | `neomutt` | yes — composes to the address |
| Mail | `notmuch` with an indexed maildir | yes — searches mail with them |
| CardDAV | `vdirsyncer` writing a vdir | yes — opens the vCard |

### The iPhone source needs a patched BlueFerry

OmaDex enumerates the phonebook with a `ListContacts` D-Bus method that
upstream BlueFerry does not have yet. Until that lands, install
**`blueferry-backend`** from the fork. The GTK, Qt and Quickshell clients are
unaffected — only that one package changes.

`omadex doctor` detects an unpatched backend and says so. Without it the
failure is silent: the phone stays paired, the daemon stays healthy, and
contacts simply never arrive.

## Install

```bash
./build.sh -si            # build and install the Arch package
omadex plugin install     # copy the overlay into ~/.config/omarchy/plugins
omarchy plugin enable omadex.contacts
omadex sync
```

Bind the overlay by adding this to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + CTRL + ALT + C", "Contacts",
       "omarchy-shell shell toggle omadex.contacts '{}'")
```

## Command line

```bash
omadex doctor                     # is each source usable, and if not, why
omadex sync                       # rebuild from every enabled source
omadex search alice               # find people
omadex show <address> --records   # one person, and what each source gave
omadex list --source iPhone       # everyone a source contributed to
omadex open <address>             # open the application a record came from
omadex review                     # merges held back for a human
omadex link <a> <b>               # these two addresses are one person
omadex unlink <a> <b>             # these two are not
omadex sources                    # what is enabled, and where it reads from
omadex sources disable Mail       # turn a source off
```

## How merging works

Only addresses identify a person. Names never merge anyone — they are used to
*withhold* a merge, never to make one — and neither do street addresses, since
housemates share those.

- A **shared email** merges two records outright.
- A **shared phone** merges them only if their names agree, because colleagues
  share switchboards. Disagreeing names go to `omadex review`.
- A destination on four or more records under three or more names is a
  switchboard and merges nobody.
- Records with no address at all merge on an exact name match, which is safe
  precisely because they carry no address to attach to the wrong person.

Your `link` and `unlink` decisions live in the one table a sync never clears.

## Configuration

`~/.config/omadex/settings.json` holds only what you changed; everything else
follows the defaults, so upgrades reach you. `ctrl+,` in the overlay shows each
source, what it contributed, and where it reads from.

## Data

Contacts live in `~/.local/state/omadex/contacts.sqlite` (0600, in a 0700
directory), rebuilt from the sources on every sync. OmaDex sends nothing
anywhere and has no network code.

`omadexd` is packaged but not enabled. The overlay uses the CLI; the daemon
exists to serve `io.omadex.Contacts1` to other clients, and is opt-in:

```bash
systemctl --user enable --now omadex
```

## Licence

MIT.
