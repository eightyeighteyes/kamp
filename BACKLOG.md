# Backlog
## Optimization: Check existing tags / embedded image to see if they even need to be updated

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

## Human Readable Bandcamp State

...

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
