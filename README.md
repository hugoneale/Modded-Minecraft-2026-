# Modded-Minecraft-2026- (16MARSHAL modpack)

A 221-mod kitchen-sink pack (Create, Mekanism, AE2, Tinkers, Ice and Fire, Alex's Mobs/Caves, Apotheosis, Iron's Spells, Ars Nouveau, Twilight Forest, Aether, and more). It includes all configs, KubeJS scripts and resource packs.

| | |
|---|---|
| Minecraft | **1.20.1** |
| Mod loader | **Forge 47.4.10** |
| Recommended RAM | 8 GB+ |
| Mods | 221 (see [MODLIST.md](MODLIST.md)) |

## ⚒️ Modsmith app for Windows (easiest)

**[Download Modsmith.exe](https://github.com/hugoneale/Modded-Minecraft-2026-/releases/latest/download/Modsmith.exe)**: one file, nothing to install, no Python needed. Double-click it.

The app opens with this pack's 221 mods already loaded. You can untick mods you don't want, add others by searching Modrinth or CurseForge, pick any Minecraft version and loader, then check, install, update or force-port everything. See [Modsmith](#modsmith-update-or-port-the-mods-to-another-minecraft-version) below.

> The first time, Windows SmartScreen may say "Windows protected your PC" because the app isn't code-signed. Click **More info**, then **Run anyway**.

## ⬇️ Download the modpack

**[Download 16MARSHALST.zip](https://github.com/hugoneale/Modded-Minecraft-2026-/raw/main/16MARSHALST.zip)**

This is a CurseForge-format modpack zip (about 0.8 MB). It contains a manifest, configs and scripts. The mod `.jar` files aren't inside it: your launcher downloads them automatically from CurseForge when you import the zip.

## Setup

### CurseForge app (easiest)
1. Install the [CurseForge app](https://www.curseforge.com/download/app) and open **Minecraft**.
2. Click **Create Custom Profile**, then the **import** link next to it.
3. Select `16MARSHALST.zip`. CurseForge installs Forge and all 221 mods.
4. Open the profile's settings and set allocated memory to **8 GB or more**.
5. Click **Play**.

### Prism Launcher / ATLauncher / MultiMC
1. Click **Add Instance** → **Import** (Prism: *Import from zip*).
2. Choose `16MARSHALST.zip`. The launcher reads the CurseForge manifest and downloads every mod.
3. Set max memory to 8 GB+ in the instance settings and launch.

> If a launcher reports that some mods "could not be downloaded automatically", those mod authors have disabled third-party downloads. Download each listed file from the link the launcher shows and drop it into the instance's `mods` folder.

### Dedicated server
1. Install **Forge 1.20.1-47.4.10** with the server installer from [files.minecraftforge.net](https://files.minecraftforge.net/net/minecraftforge/forge/index_1.20.1.html).
2. Copy the `mods`, `config`, `kubejs` folders from a working client instance into the server folder.
3. Remove client-only mods (for example Embeddium, Oculus, Xaero's Minimap, Mouse Tweaks, AppleSkin, Sodium/Embeddium Options and Dynamic Lights).

## Modsmith: update or port the mods to another Minecraft version

The [`modsmith/`](modsmith/) folder contains **Modsmith**, a desktop app that:
- installs and updates mods for any Minecraft version you pick
- checks for missing dependencies and version mismatches
- uses the official build when one exists, and checks that it still has all the content your version has
- otherwise **force-ports** the mod from its source code with an AI coding agent, accepting the result only if it builds and keeps every item, block, recipe and worldgen feature
- optionally (with a warning) decompiles mods that have no public source

**Windows: [download Modsmith.exe](https://github.com/hugoneale/Modded-Minecraft-2026-/releases/latest/download/Modsmith.exe)** and double-click it. It comes with this pack built in.
On macOS or Linux, **[download the repository ZIP](https://github.com/hugoneale/Modded-Minecraft-2026-/archive/refs/heads/main.zip)**, unzip it, then run `modsmith/run-modsmith.bat` (Windows), `run-modsmith.command` (macOS) or `run-modsmith.sh` (Linux). See [modsmith/README.md](modsmith/README.md) for details.

To check this pack against the latest version, use **Import pack** with `16MARSHALST.zip`, then click **Check**.

## About updating to Minecraft 26.x

The latest Minecraft Java release is **26.3** (September 2026). This pack **stays on 1.20.1**, because the mods can't be forced onto 26.x:

- Mods are compiled against one specific Minecraft version and loader. Between 1.20.1 and 26.x Mojang rewrote big parts of the game code, including item data components, networking, registries and rendering. Editing a mod's version number would only make the game crash on launch.
- Most of this pack's ecosystem moved from Forge to **NeoForge**, and Forge and NeoForge mods are not interchangeable on newer versions.
- A 26.x version of the pack needs a real 26.x build of each mod: either released by its author, or ported from its source code (which is what Modsmith's force-port does). Many small add-ons and compat mods are never updated by their authors, and closed-source ones can only be decompiled for personal use.

Modsmith checks this for every mod and can port the ones that have source code available. A live check of this pack (October 2026) found official Minecraft 26.3 builds for about 57 of the 221 mods, about 137 with no 26.3 build yet, and about 30 it couldn't match without a CurseForge API key. Of the versions after 1.20.1, **1.21.1** has the widest support (about 166 of the mods).
