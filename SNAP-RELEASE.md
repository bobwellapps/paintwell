# Releasing Paintwell as a snap

Everything the snap needs is already in this folder:

| File | What it is |
|---|---|
| `snap/snapcraft.yaml` | The snap recipe: name, description, permissions, and how to build it |
| `snap/gui/paintwell.desktop` | The menu entry |
| `snap/gui/paintwell.png` | The icon (512 x 512) |
| `pyproject.toml` | Lets the build install Paintwell as a normal Python app |
| `packaging/licenses/` | Licence texts shown under Help > About > Licences |
| `snap-tools/` | Three scripts that do the setup, build and publishing for you |

## Quick start: three scripts

Unzip the Paintwell source somewhere, for example `~/Projects/paintwell`, then open a
terminal in that folder (in Dolphin: right-click inside the folder > Open Terminal Here).

| Step | Run | What it does |
|---|---|---|
| 1 | `./snap-tools/1-setup.sh` | Installs Snapcraft and LXD. **Only needed once.** Afterwards, **log out and back in.** |
| 2 | `./snap-tools/2-build.sh` | Builds `paintwell_1.1_amd64.snap` and offers to install it on this computer so you can try it. |
| 3 | `./snap-tools/3-publish.sh` | Logs you in to the Snap Store, reserves the name, uploads to the `edge` testing channel, and can then move it to `stable` for everyone. |

Before step 3, make a free account at https://snapcraft.io (it uses an Ubuntu One login).

The licence (GPL-3.0-or-later) is already filled in. If Paintwell's code is on GitHub, you
can also remove the `#` from the `website`, `source-code` and `issues` lines in
`snap/snapcraft.yaml`, and correct the addresses.

The rest of this guide explains each step in more detail, if you'd rather type the commands
yourself or something goes wrong.

## 1. Install the build tools (once)

On your Kubuntu machine:

```
sudo snap install snapcraft --classic
sudo snap install lxd
sudo lxd init --auto
sudo usermod -aG lxd $USER
```

Then log out and back in, so your account can use LXD. Snapcraft builds the snap inside a
clean Ubuntu 24.04 container, so nothing on your own system is changed.

## 2. Build the snap

In this folder (the one that holds `snap/` and `pyproject.toml`):

```
snapcraft pack
```

The first build downloads Ubuntu 24.04 and the Python packages, so it takes a while. When
it finishes you'll have `paintwell_1.1_amd64.snap`.

## 3. Install it and test it

```
sudo snap install --dangerous ./paintwell_1.1_amd64.snap
paintwell
```

(`--dangerous` only means "not from the store yet".) Paintwell also appears in your app menu.

Things to check:
- **Opening and saving:** files in your home folder work straight away. For USB sticks and
  other drives, run `sudo snap connect paintwell:removable-media`. The store can turn this on
  automatically later, if you ask (see step 6).
- **Printing:** File > Print should list your printers.
- **Scanning with a USB scanner:** run `sudo snap connect paintwell:raw-usb` first, as snaps
  aren't allowed direct USB access by default. Network scanners use the `network` and
  `avahi-observe` permissions, which connect automatically.

To remove the test copy: `sudo snap remove paintwell`

## 4. Create a store account and reserve the name

1. Sign up at https://snapcraft.io (it uses an Ubuntu One account).
2. Log in from the terminal:
   ```
   snapcraft login
   ```
3. Reserve the name:
   ```
   snapcraft register paintwell
   ```
   If the name is already taken, you'll be told straight away. Pick another name and change
   `name:` at the top of `snap/snapcraft.yaml` to match.

## 5. Upload it

It's safest to upload to the `edge` channel first. This is a testing channel that only
people who ask for it get:

```
snapcraft upload --release=edge paintwell_1.1_amd64.snap
```

The store checks the snap automatically. If it finds a problem, it says what and how to fix
it. Then install it from the store, on another computer if you can:

```
sudo snap install paintwell --edge
```

When you're happy, move that upload to `stable`, the channel everyone gets. The upload
command prints a revision number, for example 1:

```
snapcraft release paintwell 1 stable
```

## 6. The store page

At https://snapcraft.io/paintwell/listing you can add screenshots, a longer description, a
contact address and the category (Art and Design / Photo and Video).

Ask for USB-drive and scanner permissions to connect automatically: post on
https://forum.snapcraft.io in the "store-requests" category. Say what the permission is for:
`removable-media` to open and save images on external drives, and `raw-usb` for scanning.

## Later updates

1. Change `version:` in `snap/snapcraft.yaml`, and `VERSION` in `paintwell/mainwindow.py`.
2. Run `snapcraft pack`.
3. Run `snapcraft upload --release=edge paintwell_<version>_amd64.snap`, test it, then
   `snapcraft release paintwell <revision> stable`.

You can also let the store build it for you instead: put the code on GitHub, then link the
repository at https://snapcraft.io/build. Each change you push is built automatically.
