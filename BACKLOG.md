## Backlog

### Homebrew Installable

*I want to be able to install tune-shifter through homebrew*

A github action process exists to run CI.  CI runs tests and validates formatting.

A github action process exists to make a release.

Releases are published to homebrew.

A user can run `brew install tune-shifter` to download and install tune-shifter.

USAGE.md is displayed after install.

### Bandcamp Album Art

*If an archive already has high quality album art, I want to use that (as long as it's not too big)*

The ideal album art dimensions are at least 1000x1000 and the ideal album art size is around 1MB (or whatever the user has configured).

If the art that is distributed with the archive is not larger than config.artwork.max_bytes, use that.

If the art is larger than config.artwork.max_bytes, scale it down to an acceptable size.

If the art is smaller than 1/2 config.artwork.max_bytes or doesn't meet config.artwork.min_dimension requirements, fall back to our existing image search.

### Interactive First Run Configuration

*Don't make me find the config file: on first run, as me for the config values*

### Config Arguments

*Don't make me ever edit the config file: let me set config through an argument*

Running `tune-shifter config set paths.staging` lets a user set the staging path, etc.

Running `tune-shifter config show` shows the whole config.

### Code Coverage

CI runs a code coverage tool.

The application has 95% code coverage in its testing.

### Configurable Album Art Search

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

### Improved Tag Sourcing

*I want ALL the tags from MusicBrainz*

The following is a list of tags that are currently not populated:
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

### Better Cleanup

Fully delete folders from staging after moving files to library.

### GUI / menu bar app for sync status

### Allow a user to verify tags before they're written

### Cross-platform service installation (Linux systemd, Windows Task Scheduler)

### Does Bandcamp auto-download actually work?  Test poll_interval_minutes.



