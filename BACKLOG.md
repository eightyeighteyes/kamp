# Backlog
## Process on Start

*When daemon starts, I want everything in the staging folder to be processed*

## Bug: Uncaught Attribute Type-Id

Is this a problem?  If so, fix it. If not, put in debug level logging if possible (since it's from a different library).

```
26-03-15 12:14:58  INFO      tune_shifter.tagger  Searching MusicBrainz for artist='Earthless' album='Black Heaven'
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:label>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      tune_shifter.tagger  Matched release: 'Black Heaven' (mbid=b0562a95-2dbf-419a-85d9-eca1d082f682)
```

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
