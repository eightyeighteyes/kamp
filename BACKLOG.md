# Backlog

## Improved Tag Sourcing

*I want ALL the tags from MusicBrainz*

When I validate the tags that tune-shifter writes against MusicBrainz Picard, I can see that we're not fully populating tags: in particular, Album Artist Sort Order and Artist Sort Order and Record Label are missed.

The following is a nonexhaustive list of tags that are currently not populated, based on one release I checked (Aesop Rock's "Bazooka Tooth"):
  - AcoustID
  - Album Artist Sort Order
  - Artist Sort Order
  - Artists
  - ASIN
  - Barcode
  - Catalog Number
  - Disc Number
  - MusicBrainz Artist ID
  - MusicBrainz Recording ID
  - MusicBrainz Release Artist ID
  - MusicBrainz Release Group ID
  - MusicBrainz Release ID
  - Original Release Date
  - Original Year
  - Producer
  - Record Label
  - Release Country
  - Release Status
  - Release Type
  - Script
  - Total Discs
  - Total Tracks

In case requests against MusicBrainz fail (due to server error, connection error, or rate limit exceeded), retry with an exponential backoff.

## Bug: Path Characters Included in File Names

To repro:
1. Let tune-shifter process Aesop Rock's "N.Y. Electric / Hunter Interlude.mp3" from the album Bazooka Tooth

Expected:
When file is copied to library, file structure is `Library/Aesop Rock/Bazooka Tooth/N.Y. Electric - Hunter Interlude.mp3`

Actual:
When file is copied to library, file structure is `Library/Aesop Rock/Bazooka Tooth/N.Y. Electric / Hunter Interlude.mp3` and the actual file name is `Hunter Interlude.mp3`



## Bug: No Bandcamp Section in Initial Config

> [tune-shifter] python -m tune_shifter sync
> tune_shifter.syncer  No [bandcamp] section in config — nothing to sync.

If no bandcamp config exists when sync is one, give the user an interactive prompt to create it, and then run sync.

## Bug: Cleanup Doesn't Remove Folders With Non-Music Files Remaining

Fully delete folders from staging after moving files to library, as long as the only remaining files are non-audio files.

Bug: When an archive or folder containing extra files (images, PDFs) is processed by tune-shifter, the folder isn't fully removed from staging and ends up in the error directory.

To repro: 
1. Start with an empty staging directory
2. Move The Notwist - Neon Golden.zip into staging directory
3. Let tune-shifter process it

Expected:
Staging folder is empty

Actual:
staging\The Notwist - Neon Golden can't be removed because cover.jpg is in it
The Notwist - Neon Golden is moved to staging\errors\


## Human Readable Bandcamp State

...

## Process on Start

*When daemon starts, I want everything in the staging folder to be processed*

## Best Release

*When there are multiple releases available, I want the tags for the release closest to the original physical format*

## AcoustID Support

> requires audio fingerprinting (fpcalc/chromaprint); can't be fetched from MusicBrainz

## Producer Support

> requires a recording-rels include and relationship traversal; deferred

## Config Arguments

*Don't make me ever edit the config file: let me set config through an argument*

Running `tune-shifter config set paths.staging` lets a user set the staging path, etc.

Running `tune-shifter config show` shows the whole config.

## Code Coverage

CI runs a code coverage tool.

The application has 95% code coverage in its testing.

## Switch to Poetry for dependency management

## FLAC Support

*I want FLAC to be as well supported as MP3 and M4A for tagging*

## OGG Support

*I want OGG to be as well supported as MP3 and M4A for tagging*

## One File At A Time

*I only want to copy one file into the staging folder and let tune-shifter process it*

## Nested Folders

*I want to copy a folder of folders into the staging folder and let tune-shifter process all files in all sub-folders*

## Does Bandcamp auto-download actually work?  Test poll_interval_minutes.




## Configurable Album Art Search

*I want to be able to configure where album art is retrieved from*

The default setting should be "default" and retrieve the art from musicbrainz.

*I want to be able to retrieve album art from bandcamp*

The configuration setting for this should be `"bandcamp"`

*I want to be able to retrieve album art from iTunes / Apple Music*

The configuration setting for this should be `"apple"`.

*I want to be able to retrieve album art from Spotify*

The configuration setting for this should be `"spotify"`.

*I want to be able to retrieve album art from Qobuz*

The configuration settings for this should be `"qobuz"`

## GUI / menu bar app for sync status

## Allow a user to verify tags before they're written

## Cross-platform service installation (Linux systemd, Windows Task Scheduler)
